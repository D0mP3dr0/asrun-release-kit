"""Command line: asrun <command> [options]. Every command except init reads asrun.yaml."""
import argparse
import json
import pathlib
import sys

from . import __version__, core
from .config import Config, write_template


def _cfg(a):
    return Config(a.config)


def cmd_init(a):
    p = write_template(a.config, a.project)
    print(f"wrote {p}; edit sources, layout, paths and scan, then run 'asrun assemble'")


def cmd_assemble(a):
    from .assemble import assemble
    rows = assemble(_cfg(a))
    print(f"{len(rows)} file(s) copied to original/ and repo/; ledger written")


def cmd_strip(a):
    from .strip import strip_tree
    cfg = _cfg(a)
    vc, _ = core.verifiers(cfg)
    log = []
    n = strip_tree(cfg, vc, log)
    core.write_json(cfg.ledger / "strip.json", log)
    print(f"comments and docstrings removed from {n} file(s); syntax trees and YAML content unchanged")
    return 0


def cmd_neutralize(a):
    from .neutralize import neutralize_tree
    cfg = _cfg(a)
    log = neutralize_tree(cfg)
    core.write_json(cfg.ledger / "paths.json", log)
    print(f"{len(log)} replacement(s)")
    return 0 if core.report_compare(core.compare(cfg)) else 1


def cmd_translate(a):
    from . import translate
    cfg = _cfg(a)
    if a.extract:
        out, entries = translate.extract(cfg, everything=a.all)
        print(f"{len(entries)} literal(s) to translate written to {out}; fill 'translation', then run --apply")
        return 0
    log = translate.apply(cfg, a.entries, a.map)
    print(f"{len(log)} translation(s) applied")
    return 0 if core.report_compare(core.compare(cfg)) else 1


def cmd_scan(a):
    from .scan import scan
    if a.config and pathlib.Path(a.config).exists():
        cfg = _cfg(a)
        conf = cfg["scan"]
        prefixes = [r["prefix"] for r in cfg["paths"]["roots"]] + list(cfg["paths"]["internal_names"]) \
            + [m["value"] for m in cfg["paths"]["markers"]]
        root = pathlib.Path(a.root) if a.root else cfg.repo
    else:
        from .config import DEFAULTS
        conf, prefixes, root = dict(DEFAULTS["scan"]), [], pathlib.Path(a.root or ".")
    conf = dict(conf)
    conf["exclude"] = list(conf.get("exclude", [])) + list(a.exclude or [])
    if a.max_mb is not None:
        conf["max_file_mb"] = a.max_mb
    res = scan(root.resolve(), conf, prefixes)
    if a.json:
        core.write_json(a.json, res)
    print(json.dumps(res["summary"]))
    for k, v in res.items():
        if k in ("summary", "clean", "excluded"):
            continue
        for item in v[:20]:
            print(k, json.dumps(item, ensure_ascii=False))
    if res["excluded"]:
        print(f"excluded from the scan: {len(res['excluded'])} file(s)")
    return 0 if res["clean"] else 1


def cmd_finalize(a):
    from .finalize import finalize
    res = finalize(_cfg(a))
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 0 if res["check"]["ok"] else 1


def cmd_check(a):
    from .check import check
    cfg = _cfg(a) if a.config and pathlib.Path(a.config).exists() else None
    root = pathlib.Path(a.root) if a.root else (cfg.repo if cfg else pathlib.Path("."))
    res = check(root, cfg)
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 0 if res["ok"] else 1


def cmd_rewrite_history(a):
    from .history import write_script
    cfg = _cfg(a) if a.config and pathlib.Path(a.config).exists() else None
    tree = a.tree or (str(cfg.repo) if cfg else None)
    if not tree:
        raise SystemExit("--tree is required without a configuration")
    out = write_script(a.out, a.remote, tree, a.tag, a.message, a.author_name, a.author_email,
                       branch=a.branch, py=a.py, github_release=not a.no_release)
    print(f"wrote {out}. Nothing was run: read it, then run it by hand.")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="asrun", description="Publish research code as it ran, with proofs.")
    ap.add_argument("--version", action="version", version=f"asrun {__version__}")
    ap.add_argument("-c", "--config", default="asrun.yaml", help="project configuration (default: asrun.yaml)")
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("init", help="write a configuration template")
    s.add_argument("--project", default="my-study")
    s.set_defaults(func=cmd_init)
    sub.add_parser("assemble", help="copy the files as run into original/ and repo/").set_defaults(func=cmd_assemble)
    sub.add_parser("strip", help="remove comments and docstrings, keeping the syntax tree").set_defaults(func=cmd_strip)
    sub.add_parser("neutralize-paths", help="replace absolute roots, internal names and local values"
                   ).set_defaults(func=cmd_neutralize)
    s = sub.add_parser("translate", help="extract free-text literals, or apply their translations")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--extract", action="store_true")
    g.add_argument("--apply", action="store_true")
    s.add_argument("--all", action="store_true", help="extract English-looking literals too")
    s.add_argument("--entries", help="literals file (default: translation/literals.json)")
    s.add_argument("--map", help="JSON object {original text: translation} used to fill empty entries")
    s.set_defaults(func=cmd_translate)
    s = sub.add_parser("scan", help="list what should not be published")
    s.add_argument("root", nargs="?", help="tree to scan (default: repo/ of the configuration)")
    s.add_argument("--exclude", action="append", help="glob relative to the root; repeatable")
    s.add_argument("--max-mb", type=float)
    s.add_argument("--json", help="write the full report here")
    s.set_defaults(func=cmd_scan)
    sub.add_parser("finalize", help="write the verifiers, digests, PROVENANCE.md and SHA256SUMS"
                   ).set_defaults(func=cmd_finalize)
    s = sub.add_parser("check", help="check SHA256SUMS and run the published verifiers")
    s.add_argument("root", nargs="?")
    s.set_defaults(func=cmd_check)
    s = sub.add_parser("rewrite-history", help="write a single-commit history script (never runs it)")
    s.add_argument("--out", required=True)
    s.add_argument("--remote", required=True)
    s.add_argument("--tag", required=True)
    s.add_argument("--message", required=True)
    s.add_argument("--author-name", required=True)
    s.add_argument("--author-email", required=True)
    s.add_argument("--tree")
    s.add_argument("--branch", default="main")
    s.add_argument("--py", default="python3")
    s.add_argument("--no-release", action="store_true")
    s.set_defaults(func=cmd_rewrite_history)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.func(a) or 0


if __name__ == "__main__":
    sys.exit(main())
