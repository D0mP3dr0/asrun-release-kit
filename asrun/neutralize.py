"""Replace local absolute paths, internal names and local values by neutral forms.

Python: a string literal that starts with a configured root prefix becomes an f-string that
starts with {INTERNAL_<var>}, and the module gains one statement reading that variable from the
environment; an f-string whose first literal part starts with the prefix gets the same field;
identifiers listed in rename_identifiers are renamed (INTERNAL_ names); every other string gets
the text replacements of core.neutral_text. YAML, CSV and text records get the text replacements;
JSON records are parsed, replaced string by string and written back."""
import ast
import json
import re

from .core import neutral_text, root_of

TEXT_SUFFIXES = (".yaml", ".yml", ".csv", ".txt", ".md", ".toml", ".cfg", ".ini", ".sh")


def env_line(r):
    return f'INTERNAL_{r["var"]} = __import__("os").environ.get("{r["env"]}", ".")'


def segment(lines, n):
    b0 = lines[n.lineno - 1].encode("utf-8")
    head = b0[:n.col_offset].decode("utf-8")
    tail = lines[n.end_lineno - 1].encode("utf-8")[n.end_col_offset:].decode("utf-8")
    if n.lineno == n.end_lineno:
        mid = b0[n.col_offset:n.end_col_offset].decode("utf-8")
    else:
        parts = [b0[n.col_offset:].decode("utf-8")] + lines[n.lineno:n.end_lineno - 1] + \
                [lines[n.end_lineno - 1].encode("utf-8")[:n.end_col_offset].decode("utf-8")]
        mid = "\n".join(parts)
    return head, mid, tail


def _fliteral(var, rest):
    body = rest.replace("{", "{{").replace("}", "}}").replace("\\", "\\\\")
    body = body.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    q = '"' if '"' not in body else "'"
    if q in body:
        body = body.replace(q, "\\" + q)
    return "f" + q + "{INTERNAL_" + var + "}" + body + q


def _protected(tree):
    inside_f = {id(c) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for c in ast.walk(n) if c is not n}
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return inside_f, docs


def neutralize_py(src, cfg):
    tree = ast.parse(src)
    inside_f, docs = _protected(tree)
    edits, used = [], {}
    renames = cfg["paths"]["rename_identifiers"]
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in inside_f and id(n) not in docs:
            r, rest = root_of(n.value, cfg)
            if r:
                edits.append((n, _fliteral(r["var"], neutral_text(rest, cfg)), "root_in_literal"))
                used[r["var"]] = r
            elif neutral_text(n.value, cfg) != n.value:
                edits.append((n, repr(neutral_text(n.value, cfg)), "text_in_literal"))
        elif isinstance(n, ast.JoinedStr) and id(n) not in inside_f:
            first = n.values[0] if n.values else None
            r = root_of(first.value, cfg)[0] if isinstance(first, ast.Constant) else None
            changed = any(isinstance(v, ast.Constant) and neutral_text(v.value, cfg) != v.value for v in n.values) \
                or any(isinstance(c, ast.Name) and c.id in renames for c in ast.walk(n))
            if r or changed:
                edits.append((n, r, "fstring"))
                if r:
                    used[r["var"]] = r
        elif isinstance(n, ast.Name) and n.id in renames and id(n) not in inside_f:
            edits.append((n, renames[n.id], "rename"))
    if not edits:
        return src, []
    lines = src.split("\n")
    log = []
    for n, new, kind in sorted(edits, key=lambda t: (t[0].lineno, t[0].col_offset), reverse=True):
        head, mid, tail = segment(lines, n)
        if kind == "fstring":
            r = new
            new = mid
            if r:
                if r["prefix"] not in mid or "\\" in r["prefix"]:
                    raise SystemExit(f"line {n.lineno}: f-string root prefix needs a manual edit")
                new = new.replace(r["prefix"], "{INTERNAL_" + r["var"] + "}", 1)
            for old, nn in renames.items():
                new = re.sub(r"\b" + re.escape(old) + r"\b", nn, new)
            new = neutral_text(new, cfg)
        lines[n.lineno - 1:n.end_lineno] = [head + new + tail]
        log.append({"line": n.lineno, "kind": kind, "before": mid, "after": new})
    if used:
        k = 0
        while k < len(lines) and (lines[k].startswith("#") or lines[k].strip() == ""):
            k += 1
        tree2 = ast.parse("\n".join(lines))
        body = tree2.body
        j = 0
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            j = 1
        while j < len(body) and isinstance(body[j], ast.ImportFrom) and body[j].module == "__future__":
            j += 1
        if j:
            k = body[j - 1].end_lineno
        for var in sorted(used, reverse=True):
            lines.insert(k, env_line(used[var]))
            log.append({"line": k + 1, "kind": "env_statement", "before": "", "after": env_line(used[var])})
    out = "\n".join(lines)
    ast.parse(out)
    return out, log


def _json_walk(o, cfg):
    if isinstance(o, dict):
        return {neutral_text(k, cfg): _json_walk(v, cfg) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_walk(v, cfg) for v in o]
    if isinstance(o, str):
        return neutral_text(o, cfg)
    return o


def neutralize_tree(cfg):
    repo = cfg.repo
    log = []
    for p in sorted(repo.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(repo).as_posix()
        if rel in ("verify_code.py", "verify_payload.py"):
            continue
        if p.suffix == ".py":
            src = p.read_text(encoding="utf-8")
            new, entries = neutralize_py(src, cfg)
            for e in entries:
                log.append(dict(e, file=rel))
        elif p.suffix == ".json":
            src = p.read_text(encoding="utf-8")
            obj = json.loads(src)
            new_obj = _json_walk(obj, cfg)
            if new_obj == obj:
                continue
            indent = 2 if "\n  \"" in src else (1 if "\n \"" in src else None)
            new = json.dumps(new_obj, indent=indent, ensure_ascii=False) + ("\n" if src.endswith("\n") else "")
            log.append({"file": rel, "kind": "json_text"})
        elif p.suffix in TEXT_SUFFIXES:
            src = p.read_text(encoding="utf-8")
            new = neutral_text(src, cfg)
            if new != src:
                log.append({"file": rel, "kind": "text"})
        else:
            continue
        if new != src:
            p.write_text(new, encoding="utf-8")
    return log
