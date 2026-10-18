"""Remove comments and docstrings from Python sources (and full-line comments from YAML files)
without touching the code or the parsed content."""
import ast
import io
import re
import tokenize

PRAGMA = re.compile(r"#\s*(noqa|type:|pragma|fmt:|pylint:|isort:)")
COOKIE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")


def _docstring_nodes(tree):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                out.append((b[0], len(b) == 1, isinstance(node, ast.Module)))
    return out


def strip_source(src, shebang="#!/usr/bin/env python3"):
    tree = ast.parse(src)
    lines = src.split("\n")
    cuts = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        (ln, col), txt = tok.start, tok.string
        if ln == 1 and txt.startswith("#!"):
            continue
        if ln <= 2 and COOKIE.match(lines[ln - 1]):
            continue
        if PRAGMA.match(txt):
            continue
        cuts[ln] = col
    drop = set()
    for ln, col in cuts.items():
        before = lines[ln - 1][:col]
        if before.strip() == "":
            drop.add(ln)
        else:
            lines[ln - 1] = before.rstrip()
    for node, alone, is_module in sorted(_docstring_nodes(tree), key=lambda t: -t[0].lineno):
        l0, l1 = node.lineno, node.end_lineno
        rest = lines[l1 - 1][node.end_col_offset:]
        prefix = lines[l0 - 1][:node.col_offset]
        if prefix.strip() != "" or (rest.strip() != "" and not rest.strip().startswith("#")):
            raise ValueError(f"docstring shares a line with code (line {l0}); edit by hand")
        drop.update(range(l0, l1 + 1))
        if alone and not is_module:
            drop.discard(l0)
            lines[l0 - 1] = prefix + "pass"
    new = [ln for i, ln in enumerate(lines, 1) if i not in drop]
    if new and new[0].startswith("#!") and shebang:
        new[0] = shebang
    txt = "\n".join(new)
    txt = re.sub(r"\n{4,}", "\n\n\n", txt)
    if not txt.startswith("#!"):
        txt = txt.lstrip("\n")
    if not txt.endswith("\n"):
        txt += "\n"
    return txt


def _dump_without_docstrings(src, vc):
    tree = vc.drop_docstrings_and_env(ast.parse(src))
    return ast.dump(tree, include_attributes=False)


def same_code(a, b, vc):
    """True when both sources have the same syntax tree once docstrings are dropped
    (every string, name and number compared exactly)."""
    return _dump_without_docstrings(a, vc) == _dump_without_docstrings(b, vc)


def strip_yaml(src):
    """Drop full-line comments of a YAML file; kept only if the parsed content is unchanged."""
    import yaml
    lines = [ln for ln in src.split("\n") if not ln.lstrip().startswith("#")]
    new = "\n".join(lines)
    return new if yaml.safe_load(new) == yaml.safe_load(src) else src


def strip_tree(cfg, vc, ledger):
    repo = cfg.repo
    n = 0
    for p in sorted(list(repo.rglob("*.yaml")) + list(repo.rglob("*.yml"))):
        a = p.read_text(encoding="utf-8")
        b = strip_yaml(a)
        if a != b:
            p.write_text(b, encoding="utf-8")
            ledger.append({"file": p.relative_to(repo).as_posix(), "lines_before": a.count("\n"),
                           "lines_after": b.count("\n")})
            n += 1
    for p in sorted(repo.rglob("*.py")):
        rel = p.relative_to(repo).as_posix()
        if rel in ("verify_code.py", "verify_payload.py"):
            continue
        a = p.read_text(encoding="utf-8")
        b = strip_source(a)
        if not same_code(a, b, vc):
            raise SystemExit(f"strip changed the code of {rel}; nothing written")
        if a != b:
            p.write_text(b, encoding="utf-8")
            ledger.append({"file": rel, "lines_before": a.count("\n"), "lines_after": b.count("\n")})
            n += 1
    return n
