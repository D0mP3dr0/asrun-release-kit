import io
import json
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from contextlib import redirect_stdout

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asrun import cli, core  # noqa: E402
from asrun.config import Config  # noqa: E402
from asrun.history import write_script  # noqa: E402
from asrun.neutralize import neutralize_py  # noqa: E402
from asrun.scan import scan  # noqa: E402
from asrun.strip import same_code, strip_source  # noqa: E402

EXAMPLE = ROOT / "examples" / "tiny-study"
FAKE_ROOT = "/" + "srv/fakelab/tiny-study"


def quiet(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def run_verifier(repo, name):
    r = subprocess.run([sys.executable, "-I", name], cwd=repo, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class ExampleRelease(unittest.TestCase):
    """The whole flow on a copy of the synthetic example, then tampering tests."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        shutil.copytree(EXAMPLE, cls.tmp / "tiny-study", ignore=shutil.ignore_patterns("build"))
        cls.cfg_path = str(cls.tmp / "tiny-study" / "release" / "asrun.yaml")
        mapping = str(cls.tmp / "tiny-study" / "release" / "translations.json")
        steps = [["assemble"], ["strip"], ["neutralize-paths"], ["translate", "--extract"],
                 ["translate", "--apply", "--map", mapping], ["scan"], ["finalize"], ["check"]]
        cls.rcs = {}
        for s in steps:
            cls.rcs[" ".join(s[:2])] = quiet(["-c", cls.cfg_path] + s)[0]
        cls.repo = cls.tmp / "tiny-study" / "release" / "build" / "repo"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def fresh_copy(self):
        d = pathlib.Path(tempfile.mkdtemp()) / "repo"
        shutil.copytree(self.repo, d)
        self.addCleanup(shutil.rmtree, d.parent)
        return d

    def test_every_step_succeeds(self):
        self.assertTrue(all(rc == 0 for rc in self.rcs.values()), self.rcs)

    def test_published_tree_is_clean(self):
        text = (self.repo / "train.py").read_text()
        self.assertNotIn("fakelab", text)
        self.assertNotIn("team_notes", text)
        self.assertNotIn("#", text.split("\n", 2)[2])
        self.assertIn("INTERNAL_DATA_ROOT", text)
        self.assertIn("Final accuracy of", text)
        cfg = Config(self.cfg_path)
        self.assertTrue(scan(self.repo, cfg["scan"], [FAKE_ROOT])["clean"])

    def test_verifiers_pass(self):
        self.assertEqual(run_verifier(self.repo, "verify_code.py")[0], 0)
        self.assertEqual(run_verifier(self.repo, "verify_payload.py")[0], 0)

    def test_changed_code_is_a_mismatch(self):
        d = self.fresh_copy()
        p = d / "train.py"
        p.write_text(p.read_text().replace("SEED = 7", "SEED = 8"))
        rc, out = run_verifier(d, "verify_code.py")
        self.assertEqual(rc, 1)
        self.assertIn("MISMATCH train.py", out)

    def test_changed_operator_is_a_mismatch(self):
        d = self.fresh_copy()
        p = d / "train.py"
        p.write_text(p.read_text().replace("x >= threshold", "x > threshold"))
        self.assertEqual(run_verifier(d, "verify_code.py")[0], 1)

    def test_changed_yaml_value_is_a_mismatch(self):
        d = self.fresh_copy()
        p = d / "config.yaml"
        p.write_text(p.read_text().replace("seed: 7", "seed: 9"))
        self.assertIn("MISMATCH config.yaml", run_verifier(d, "verify_code.py")[1])

    def test_rephrased_message_is_ignored_by_design(self):
        d = self.fresh_copy()
        p = d / "train.py"
        p.write_text(p.read_text().replace("Loading the samples of the field survey", "Reading the survey samples now"))
        self.assertEqual(run_verifier(d, "verify_code.py")[0], 0)

    def test_changed_number_is_a_mismatch(self):
        d = self.fresh_copy()
        p = d / "results" / "metrics.json"
        p.write_text(p.read_text().replace("0.4648", "0.4649"))
        rc, out = run_verifier(d, "verify_payload.py")
        self.assertEqual(rc, 1)
        self.assertIn("MISMATCH results/metrics.json", out)

    def test_changed_number_inside_a_note_is_a_mismatch(self):
        d = self.fresh_copy()
        p = d / "results" / "metrics.json"
        p.write_text(p.read_text().replace("with seed 7 on", "with seed 8 on"))
        self.assertIn("MISMATCH results/metrics.json", run_verifier(d, "verify_payload.py")[1])

    def test_changed_csv_number_is_a_mismatch(self):
        d = self.fresh_copy()
        p = d / "data" / "samples.csv"
        p.write_text(p.read_text().replace("0.47,0", "0.48,0"))
        self.assertIn("MISMATCH data/samples.csv", run_verifier(d, "verify_payload.py")[1])

    def test_readme_number_must_match_the_record(self):
        d = self.fresh_copy()
        p = d / "README.md"
        p.write_text(p.read_text().replace("accuracy of 0.8125", "accuracy of 0.8215"))
        rc, out = run_verifier(d, "verify_payload.py")
        self.assertEqual(rc, 1)
        self.assertIn("CLAIM FAILS", out)

    def test_tampered_file_fails_sha256sums(self):
        d = self.fresh_copy()
        with open(d / "README.md", "a") as f:
            f.write("\n")
        from asrun.check import sha256sums
        self.assertFalse(sha256sums(d)["ok"])

    def test_check_flags_a_hand_edited_verifier(self):
        d = self.fresh_copy()
        p = d / "verify_code.py"
        p.write_text(p.read_text().replace("'min_words': 4", "'min_words': 1"))
        from asrun.check import check
        res = check(d, Config(self.cfg_path))
        self.assertFalse(res["verify_code.py"]["generated"])
        self.assertFalse(res["ok"])

    def test_sources_untouched(self):
        for p in (EXAMPLE / "as_run").rglob("*"):
            if p.is_file():
                q = self.tmp / "tiny-study" / "as_run" / p.relative_to(EXAMPLE / "as_run")
                self.assertEqual(core.sha256_file(p), core.sha256_file(q))


class Units(unittest.TestCase):
    def cfg(self, extra=None):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        conf = {"paths": {"roots": [{"prefix": FAKE_ROOT, "var": "DATA_ROOT", "env": "X_ROOT"}],
                          "internal_names": ["secret_dir"], "rename_identifiers": {"secret_dir_path": "INTERNAL_P"}}}
        conf.update(extra or {})
        (d / "asrun.yaml").write_text(json.dumps(conf))
        return Config(d / "asrun.yaml"), d

    def test_strip_round_trip(self):
        src = ('#!/usr/bin/env python3\n"""Module doc."""\nimport os  # noqa: F401\n\n\n'
               'def f(a):\n    """Only a docstring."""\n\n\nclass C:\n    """Doc."""\n    x = "# not a comment"  # comment\n')
        out = strip_source(src)
        from asrun.core import verifiers
        cfg, _ = self.cfg()
        vc, _ = verifiers(cfg)
        self.assertTrue(same_code(src, out, vc))
        self.assertIn("# noqa: F401", out)
        self.assertIn('"# not a comment"', out)
        self.assertNotIn("Doc", out)
        self.assertEqual(strip_source(out), out)

    def test_neutralize_keeps_the_digest(self):
        cfg, d = self.cfg()
        src = (f'import os\nA = "{FAKE_ROOT}/data/x.csv"\nB = f"{FAKE_ROOT}/out/{{os.sep}}.json"\n'
               'secret_dir_path = "secret_dir/a"\nprint(f"{secret_dir_path} and some words here")\n')
        new, log = neutralize_py(src, cfg)
        self.assertNotIn("fakelab", new)
        self.assertNotIn("secret_dir", new)
        (d / "o.py").write_text(src)
        (d / "p.py").write_text(new)
        vc, _ = core.verifiers(cfg)
        self.assertEqual(core.original_digest(d / "o.py", cfg, vc), core.published_digest(d / "p.py", vc))
        (d / "p.py").write_text(new.replace("x.csv", "y.csv"))
        self.assertNotEqual(core.original_digest(d / "o.py", cfg, vc), core.published_digest(d / "p.py", vc))

    def test_translation_must_keep_words_and_numbers(self):
        from asrun.translate import _check_translation
        cfg, _ = self.cfg()
        with self.assertRaises(ValueError):
            _check_translation({"kind": "str", "file": "a.py", "text": "uma frase com quatro palavras",
                                "translation": "too short"}, cfg)
        with self.assertRaises(ValueError):
            _check_translation({"kind": "json", "file": "a.json", "pointer": ["n"], "text": "obtido com 16 amostras hoje",
                                "translation": "obtained with 17 samples today"}, cfg)

    def test_scan_finds_each_kind(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        (d / "a.py").write_text('# isto não é inglês\nP = "' + "/" + 'home/someone/x"\nE = "a.b' + "@" + 'example.org"\n'
                                'T = "zebracorn"\n')
        (d / "big.bin").write_bytes(b"\0" * 2048)
        chunk = b"Author\x00Someone"
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(chunk)) + b"tEXt" + chunk + \
            struct.pack(">I", zlib.crc32(b"tEXt" + chunk)) + struct.pack(">I", 0) + b"IEND" + b"\0\0\0\0"
        (d / "f.png").write_bytes(png)
        with zipfile.ZipFile(d / "w.docx", "w") as z:
            z.writestr("docProps/core.xml", "<cp:coreProperties><dc:creator>Someone</dc:creator></cp:coreProperties>")
        (d / "r.pdf").write_bytes(b"%PDF-1.4\n1 0 obj << /Author (Someone) >> endobj\n")
        from asrun.config import DEFAULTS
        conf = dict(DEFAULTS["scan"], forbidden_terms=["zebracorn"], max_file_mb=0.001)
        res = scan(d, conf)
        s = res["summary"]
        for k in ("forbidden_term", "absolute_path", "email", "non_english_comment", "large_file"):
            self.assertGreaterEqual(s[k], 1, k)
        self.assertEqual({x["file"] for x in res["author_metadata"]}, {"f.png", "w.docx", "r.pdf"})
        self.assertFalse(res["clean"])

    def test_rewrite_history_only_writes_a_script(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        out = write_script(d / "rewrite.sh", "https://example.org/x.git", d, "v0.1.0", "release 0.1.0",
                           "Author Name", "author" + "@" + "example.org")
        text = out.read_text()
        self.assertIn("git push --force origin main", text)
        self.assertIn('git tag -a "$TAG"', text)
        self.assertTrue(out.stat().st_mode & 0o100)
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["rewrite.sh"])

    def test_init_template_loads(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        rc, _ = quiet(["-c", str(d / "asrun.yaml"), "init", "--project", "demo"])
        self.assertEqual(rc, 0)
        self.assertEqual(Config(d / "asrun.yaml")["project"], "demo")


if __name__ == "__main__":
    unittest.main()
