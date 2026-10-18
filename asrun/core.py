"""Shared pieces: hashing, the verifier templates loaded as modules, and the private side of the
digests (the rules that map a file as run onto its published form)."""
import ast
import hashlib
import importlib.util
import json
import pathlib
import pprint
import sys

TEMPLATES = pathlib.Path(__file__).resolve().parent / "templates"
CODE_SUFFIXES = (".py", ".yaml", ".yml")
PAYLOAD_SUFFIXES = (".json", ".csv")


def sha256_file(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def write_json(path, obj):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path, default=None):
    path = pathlib.Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def code_config(cfg):
    return {"min_words": cfg["text"]["min_words"], "neutral_prefix": "INTERNAL_",
            "neutral_name": "INTERNAL_NAME", "placeholder": "<text>"}


def payload_config(cfg):
    p = cfg["payload"]
    return {"min_words": cfg["text"]["min_words"], "key_translations": p["key_translations"],
            "readme": p["readme"], "readme_claims": p["readme_claims"]}


def render(template_name, conf):
    """Source text of a published verifier: the template with its CONFIG line replaced."""
    src = (TEMPLATES / template_name).read_text(encoding="utf-8")
    lines = src.split("\n")
    idx = [i for i, ln in enumerate(lines) if ln.startswith("CONFIG = ")]
    if len(idx) != 1:
        raise RuntimeError(f"template {template_name} must have one CONFIG line")
    lines[idx[0]] = "CONFIG = " + pprint.pformat(conf, width=100, sort_dicts=True)
    out = "\n".join(lines)
    ast.parse(out)
    return out


def load_module(name, source):
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(source, name, "exec"), mod.__dict__)
    return mod


def verifiers(cfg):
    vc = load_module("asrun_verify_code", render("verify_code.py", code_config(cfg)))
    vp = load_module("asrun_verify_payload", render("verify_payload.py", payload_config(cfg)))
    return vc, vp


def neutral_text(s, cfg):
    """The text replacements applied outside Python syntax (and to every string in Python)."""
    p = cfg["paths"]
    for m in p["markers"]:
        s = s.replace(m["value"], m["marker"])
    for r in sorted(p["roots"], key=lambda r: -len(r["prefix"])):
        s = s.replace(r["prefix"], "${" + r["env"] + "}")
    for n in p["internal_names"]:
        s = s.replace(n, p["neutral_folder"])
    return s


def root_of(s, cfg):
    for r in sorted(cfg["paths"]["roots"], key=lambda r: -len(r["prefix"])):
        pre = r["prefix"]
        if s == pre or s.startswith(pre + "/") or s.startswith(pre + "\\"):
            return r, s[len(pre):]
    return None, None


class PrivateText(ast.NodeTransformer):
    """On the file as run: a literal that starts with a root prefix becomes <VAR> + rest; every
    other string gets the same text replacements that the published file received."""

    def __init__(self, cfg):
        self.cfg = cfg

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            r, rest = root_of(node.value, self.cfg)
            new = "<" + r["var"] + ">" + neutral_text(rest, self.cfg) if r else neutral_text(node.value, self.cfg)
            if new != node.value:
                return ast.Constant(value=new)
        return node


class PrivateRename(ast.NodeTransformer):
    def __init__(self, cfg):
        self.names = cfg["paths"]["rename_identifiers"]

    def visit_Name(self, node):
        if node.id in self.names:
            node.id = self.names[node.id]
        return node


def original_digest(path, cfg, vc):
    path = str(path)
    if path.endswith((".yaml", ".yml")):
        return vc.yaml_sha256(path, text=lambda s: neutral_text(s, cfg))
    return vc.code_sha256(path, before=(PrivateText(cfg), PrivateRename(cfg)))


def original_payload(path, rel, cfg, vp):
    return vp.payload_sha256(path, rel, text=lambda s: neutral_text(s, cfg), keymap={})


def published_digest(path, vc):
    return vc.file_sha256(str(path))


def published_payload(path, rel, vp):
    return vp.payload_sha256(path, rel)


def repo_files(root, skip_git=True):
    root = pathlib.Path(root)
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        if skip_git and (rel.startswith(".git/") or rel == ".git"):
            continue
        out.append(rel)
    return out


def compare(cfg, rels=None):
    """Compare every code and payload file of repo/ with original/. Returns a list of dicts."""
    vc, vp = verifiers(cfg)
    orig, repo = cfg.original, cfg.repo
    out = []
    for rel in rels if rels is not None else repo_files(repo):
        o, d = orig / rel, repo / rel
        if not o.exists() or not d.exists():
            continue
        try:
            if rel.endswith(CODE_SUFFIXES):
                a, b = original_digest(o, cfg, vc), published_digest(d, vc)
                kind = "code"
            elif rel.endswith(PAYLOAD_SUFFIXES):
                a, b = original_payload(o, rel, cfg, vp), published_payload(d, rel, vp)
                kind = "payload"
            else:
                a, b = sha256_file(o), sha256_file(d)
                kind = "bytes"
        except (SyntaxError, ValueError) as e:
            out.append({"file": rel, "kind": "error", "ok": False, "error": repr(e)[:200]})
            continue
        out.append({"file": rel, "kind": kind, "ok": a == b, "as_run": a, "published": b})
    return out


def report_compare(results, stream=None):
    stream = stream or sys.stdout
    bad = [r for r in results if not r["ok"]]
    for r in bad:
        print(f"DIFFERS {r['file']} ({r['kind']})", file=stream)
    print(f"{len(results) - len(bad)}/{len(results)} files identical under the published rules", file=stream)
    return not bad
