"""Close the release: copy the files written for it, write the two verifiers, CODE_AST_SHA256,
PAYLOAD_SHA256, PROVENANCE.md, .gitattributes and SHA256SUMS. Stops on the first file whose
code or payload differs from the file as run under the published rules."""
import sys

from . import core
from .check import check

GENERATED = {"SHA256SUMS", "CODE_AST_SHA256", "PAYLOAD_SHA256", "PROVENANCE.md", ".gitattributes",
             "verify_code.py", "verify_payload.py"}
GITATTRIBUTES = "* -text\n"


def _provenance(cfg, rows, ledgers):
    strip_n = len(ledgers["strip"])
    paths = ledgers["paths"]
    roots_used = sorted({e["after"] for e in paths if e.get("kind") == "env_statement"})
    tr = ledgers["translate"]
    n_tr = len([e for e in tr if "before" in e])
    keys = sorted({e["file"] for e in tr if "keys_translated" in e})
    markers = [m["marker"] for m in cfg["paths"]["markers"]]
    P = ["# Provenance", "",
         "For every file of the repository: where it comes from, the SHA-256 of the file as run (or as written),",
         "the SHA-256 of the file published here, and how the two are proven equivalent. `SHA256SUMS` carries the",
         "published digests in the format read by `sha256sum -c`.", "", "## What differs from the files as run", ""]
    if strip_n:
        P.append(f"- Comments and docstrings were removed from {strip_n} file(s) (Python sources; full-line")
        P.append("  comments of YAML files).")
    if roots_used:
        P.append("- An absolute data-root prefix hard-coded in the code was replaced by a variable read from the")
        P.append("  environment, in one statement added at the top of each such file:")
        P += [f"  `{s}`" for s in roots_used]
    if any(e.get("kind") in ("text", "json_text", "text_in_literal") for e in paths) or cfg["paths"]["internal_names"]:
        P.append(f"- Local folder names were replaced by `{cfg['paths']['neutral_folder']}`, and absolute prefixes outside")
        P.append("  Python code by `${<VARIABLE>}`.")
    if markers:
        P.append("- Local values were replaced by the placeholders " + ", ".join(f"`{m}`" for m in markers) + ".")
    if cfg["paths"]["rename_identifiers"]:
        P.append("- Identifiers were renamed to names starting with `INTERNAL_`.")
    if n_tr:
        P.append(f"- {n_tr} free-text string(s) ({cfg['text']['min_words']} or more words) were translated into English.")
    if keys:
        P.append("- Keys were translated in " + ", ".join(f"`{k}`" for k in keys) + "; `verify_payload.py` maps them back.")
    for rel, note in sorted(cfg["publish"]["declared_changes"].items()):
        P.append(f"- `{rel}`: {note}")
    P += ["", "`verify_code.py` and `verify_payload.py` state exactly which parts their digests ignore.", "",
          "## Every file", "", "| file | source | sha256 as run | sha256 published | check |", "| --- | --- | --- | --- | --- |"]
    for r in rows:
        so = f"`{r['sha_as_run']}`" if r["sha_as_run"] != "n/a" else "n/a"
        P.append(f"| `{r['file']}` | {r['source']} | {so} | `{r['sha_published']}` | {r['check']} |")
    return "\n".join(P) + "\n"


def finalize(cfg):
    repo, orig = cfg.repo, cfg.original
    led = core.read_json(cfg.ledger / "assemble.json")
    if led is None:
        raise SystemExit("run 'asrun assemble' first")
    source_of = {x["file"]: x["source"] for x in led["files"]}
    ledgers = {k: core.read_json(cfg.ledger / f"{k}.json", []) for k in ("strip", "paths", "translate")}
    new_files = cfg["publish"]["new_files"]
    for rel, src in new_files.items():
        t = repo / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(cfg.where(src).read_bytes())
    for d in cfg["publish"]["keep_empty_dirs"]:
        (repo / d).mkdir(parents=True, exist_ok=True)
        (repo / d / ".gitkeep").write_bytes(b"")
    (repo / "verify_code.py").write_text(core.render("verify_code.py", core.code_config(cfg)) , encoding="utf-8")
    (repo / "verify_payload.py").write_text(core.render("verify_payload.py", core.payload_config(cfg)), encoding="utf-8")
    (repo / ".gitattributes").write_text(GITATTRIBUTES, encoding="utf-8")
    vc, vp = core.verifiers(cfg)
    code, payload, rows, errors = [], [], [], []
    declared = cfg["publish"]["declared_changes"]
    for rel in core.repo_files(repo):
        if rel in GENERATED - {"verify_code.py", "verify_payload.py"}:
            continue
        d = repo / rel
        if rel in new_files or rel in ("verify_code.py", "verify_payload.py") or rel.endswith("/.gitkeep"):
            rows.append({"file": rel, "source": "new", "sha_as_run": "n/a", "sha_published": core.sha256_file(d),
                         "check": "written for this release"})
            continue
        o = orig / rel
        if not o.exists():
            errors.append(f"no file as run for {rel} (list it in publish.new_files)")
            continue
        so, sd = core.sha256_file(o), core.sha256_file(d)
        row = {"file": rel, "source": source_of.get(rel, "?"), "sha_as_run": so, "sha_published": sd}
        try:
            if rel.endswith(core.CODE_SUFFIXES):
                a, b = core.original_digest(o, cfg, vc), core.published_digest(d, vc)
                if a != b:
                    errors.append(f"code differs: {rel}")
                code.append((a, rel))
                row["check"] = "byte-identical" if so == sd else f"code digest `{a[:16]}`"
            elif rel.endswith(core.PAYLOAD_SUFFIXES):
                a, b = core.original_payload(o, rel, cfg, vp), core.published_payload(d, rel, vp)
                if a != b:
                    errors.append(f"payload differs: {rel}")
                payload.append((a, rel))
                row["check"] = "byte-identical" if so == sd else f"payload digest `{a[:16]}`"
            elif so == sd:
                row["check"] = "byte-identical"
            elif rel in declared:
                row["check"] = declared[rel]
            else:
                errors.append(f"bytes differ and no rule or declared change: {rel}")
        except (SyntaxError, ValueError) as e:
            errors.append(f"cannot read {rel}: {e!r}"[:200])
        rows.append(row)
    if errors:
        print("\n".join(errors[:50]), file=sys.stderr)
        raise SystemExit(f"{len(errors)} error(s); nothing finalized")
    version = "%d.%d" % sys.version_info[:2]
    (repo / "CODE_AST_SHA256").write_text(f"# python {version}\n" + "".join(f"{h}  {r}\n" for h, r in code),
                                          encoding="utf-8")
    (repo / "PAYLOAD_SHA256").write_text("".join(f"{h}  {r}\n" for h, r in payload), encoding="utf-8")
    (repo / "PROVENANCE.md").write_text(_provenance(cfg, rows, ledgers), encoding="utf-8")
    files = [r for r in core.repo_files(repo) if r != "SHA256SUMS"]
    (repo / "SHA256SUMS").write_text("".join(f"{core.sha256_file(repo / r)}  ./{r}\n" for r in files), encoding="utf-8")
    result = {"files": len(files) + 1, "code_digests": len(code), "payload_digests": len(payload)}
    result["check"] = check(repo, cfg)
    core.write_json(cfg.ledger / "finalize.json", result)
    return result
