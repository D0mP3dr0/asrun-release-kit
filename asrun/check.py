"""Check a release tree: SHA256SUMS, the two published verifiers (run in a clean interpreter),
that they are self-sufficient (standard library, plus PyYAML only for YAML files), that they are
the ones the kit generates for this configuration, and that they quote no local value."""
import ast
import hashlib
import pathlib
import subprocess
import sys

from . import core

ALLOWED_THIRD_PARTY = {"yaml"}


def sha256sums(repo):
    path = repo / "SHA256SUMS"
    if not path.exists():
        return {"ok": False, "detail": "SHA256SUMS missing"}
    bad, n = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(None, 1)
        rel = rel.strip().lstrip("*")
        p = repo / rel
        n += 1
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            bad.append(rel)
    listed = {ln.split(None, 1)[1].strip()[2:] for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}
    unlisted = [r for r in core.repo_files(repo) if r != "SHA256SUMS" and r not in listed]
    return {"ok": not bad and not unlisted, "files": n, "bad": bad, "unlisted": unlisted}


def imports(src):
    out = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def run(repo, name):
    r = subprocess.run([sys.executable, "-I", name], cwd=repo, capture_output=True, text=True, timeout=600)
    for p in repo.rglob("__pycache__"):
        for f in p.iterdir():
            f.unlink()
        p.rmdir()
    return {"ok": r.returncode == 0, "returncode": r.returncode, "output": (r.stdout + r.stderr).strip()[-2000:]}


def leaks(text, cfg):
    p = cfg["paths"]
    secrets = [r["prefix"] for r in p["roots"]] + list(p["internal_names"]) + [m["value"] for m in p["markers"]]
    secrets += list(p["rename_identifiers"])
    low = text.lower()
    hits = [s for s in secrets if s and s in text]
    hits += [t for t in cfg["scan"]["forbidden_terms"] if t and t.lower() in low]
    return sorted(set(hits))


def check(repo, cfg=None):
    repo = pathlib.Path(repo).resolve()
    res = {"sha256sums": sha256sums(repo)}
    ga = repo / ".gitattributes"
    res["gitattributes_no_eol_conversion"] = ga.exists() and "* -text" in ga.read_text(encoding="utf-8")
    for name, template, conf in (("verify_code.py", "verify_code.py", core.code_config),
                                 ("verify_payload.py", "verify_payload.py", core.payload_config)):
        p = repo / name
        if not p.exists():
            res[name] = {"ok": False, "detail": "missing"}
            continue
        src = p.read_text(encoding="utf-8")
        extra = imports(src) - set(sys.stdlib_module_names) - ALLOWED_THIRD_PARTY
        entry = {"self_sufficient": not extra, "third_party_imports": sorted(extra)}
        if cfg is not None:
            entry["generated"] = src == core.render(template, conf(cfg))
            entry["leaks"] = leaks(src, cfg)
        entry["run"] = run(repo, name)
        entry["ok"] = entry["self_sufficient"] and entry["run"]["ok"] and entry.get("generated", True) \
            and not entry.get("leaks")
        res[name] = entry
    res["ok"] = res["sha256sums"]["ok"] and res["gitattributes_no_eol_conversion"] \
        and res["verify_code.py"]["ok"] and res["verify_payload.py"]["ok"]
    return res
