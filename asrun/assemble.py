"""Build original/ (byte copies of the files as run, in the published layout) and repo/ (the
tree that will be cleaned), with a ledger of the source sha256 of every file."""
import glob
import pathlib
import shutil

from .core import sha256_file, write_json

GLOB_CHARS = set("*?[")


def _entry(e):
    if isinstance(e, (list, tuple)):
        e = dict(zip(("src", "dst", "why"), e))
    if "src" not in e or "dst" not in e:
        raise SystemExit(f"layout entry needs src and dst: {e}")
    if ":" not in e["src"]:
        raise SystemExit(f"layout src must be '<source key>:<path>': {e['src']}")
    return e


def expand(cfg):
    pairs = []
    for e in map(_entry, cfg["layout"]):
        key, pattern = e["src"].split(":", 1)
        root = cfg.source_root(key)
        why = e.get("why", "")
        if GLOB_CHARS & set(pattern):
            fixed = []
            for part in pathlib.PurePosixPath(pattern).parts:
                if GLOB_CHARS & set(part):
                    break
                fixed.append(part)
            base = root.joinpath(*fixed)
            hits = sorted(pathlib.Path(h) for h in glob.glob(str(root / pattern), recursive=True))
            hits = [h for h in hits if h.is_file() and "__pycache__" not in h.parts]
            if not hits:
                raise SystemExit(f"layout glob matched nothing: {e['src']}")
            for h in hits:
                pairs.append((key, h, e["dst"].rstrip("/") + "/" + h.relative_to(base).as_posix(), why))
        else:
            src = root / pattern
            if not src.is_file():
                raise SystemExit(f"layout source not found: {e['src']}")
            pairs.append((key, src, e["dst"], why))
    seen = {}
    for key, src, dst, why in pairs:
        dst = dst.lstrip("/")
        if dst in seen:
            raise SystemExit(f"two sources for {dst}: {seen[dst]} and {src}")
        seen[dst] = src
    return [(k, s, d.lstrip("/"), w) for k, s, d, w in pairs]


def assemble(cfg):
    orig, repo = cfg.original, cfg.repo
    if orig.exists() or repo.exists():
        raise SystemExit(f"{orig} or {repo} exists; remove it by hand to rebuild")
    sources = [cfg.source_root(k) for k in cfg["sources"]]
    for s in sources:
        for out in (orig, repo, cfg.ledger):
            if out == s or s in out.parents:
                raise SystemExit(f"working folder {out} lies inside the source {s}")
    rows = []
    for key, src, dst, why in expand(cfg):
        for top in (orig, repo):
            t = top / dst
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, t)
        digest = sha256_file(src)
        if sha256_file(orig / dst) != digest or sha256_file(repo / dst) != digest:
            raise SystemExit(f"copy of {src} is not byte-identical")
        rows.append({"file": dst, "source": key, "source_path": str(src.relative_to(cfg.source_root(key))),
                     "why": why, "bytes": src.stat().st_size, "sha256_as_run": digest})
    write_json(cfg.ledger / "assemble.json", {"files": rows})
    return rows
