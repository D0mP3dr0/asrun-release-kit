"""Extract free-text literals for a translator, and apply the translations without changing the
normalized syntax tree (Python) or the payload (JSON).

Only free text changes: a string with min_words or more words, which the published verifiers
mask. An f-string is presented with its fields as {0}, {1}, ... and literal braces doubled; the
translation must keep every field, in the same order. A JSON note must keep its numbers, in order."""
import ast
import io
import json
import re
import tokenize

from .core import read_json, write_json
from .lang import looks_non_english
from .neutralize import segment

FIELD = re.compile(r"\{(\d+)\}")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _free(s, cfg):
    return len(s.split()) >= cfg["text"]["min_words"]


def _skip(rel):
    return rel in ("verify_code.py", "verify_payload.py")


def extract_py(src, cfg):
    tree = ast.parse(src)
    docs = {id(x.body[0].value) for x in ast.walk(tree)
            if isinstance(x, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and x.body
            and isinstance(x.body[0], ast.Expr) and isinstance(x.body[0].value, ast.Constant)}
    inside_f = {id(c) for x in ast.walk(tree) if isinstance(x, ast.JoinedStr) for c in ast.walk(x) if c is not x}
    items = []
    for x in ast.walk(tree):
        if id(x) in docs or id(x) in inside_f:
            continue
        if isinstance(x, ast.Constant) and isinstance(x.value, str) and _free(x.value, cfg):
            items.append({"line": x.lineno, "col": x.col_offset, "kind": "str", "text": x.value, "fields": 0})
        elif isinstance(x, ast.JoinedStr):
            k, parts = 0, []
            for v in x.values:
                if isinstance(v, ast.Constant):
                    parts.append(v.value.replace("{", "{{").replace("}", "}}"))
                else:
                    parts.append("{%d}" % k)
                    k += 1
            lit = "".join(v.value if isinstance(v, ast.Constant) else "X" for v in x.values)
            if _free(lit, cfg):
                items.append({"line": x.lineno, "col": x.col_offset, "kind": "fstr", "text": "".join(parts), "fields": k})
    return sorted(items, key=lambda d: (d["line"], d["col"]))


def extract_json(obj, cfg, pointer=()):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += extract_json(v, cfg, pointer + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += extract_json(v, cfg, pointer + (i,))
    elif isinstance(obj, str) and _free(obj, cfg):
        out.append({"pointer": list(pointer), "kind": "json", "text": obj})
    return out


def extract(cfg, everything=False):
    words = cfg["scan"]["foreign_words"]
    entries = []
    for p in sorted(cfg.repo.rglob("*")):
        rel = p.relative_to(cfg.repo).as_posix()
        if not p.is_file() or _skip(rel) or "__pycache__" in p.parts:
            continue
        if p.suffix == ".py":
            items = extract_py(p.read_text(encoding="utf-8"), cfg)
        elif p.suffix == ".json":
            items = extract_json(json.loads(p.read_text(encoding="utf-8")), cfg)
        else:
            continue
        for it in items:
            it["file"] = rel
            it["looks_non_english"] = looks_non_english(FIELD.sub(" ", it["text"]), words, broad=True)
            it["translation"] = ""
            if everything or it["looks_non_english"]:
                entries.append(it)
    out = cfg.folder("translation") / "literals.json"
    write_json(out, entries)
    return out, entries


def fields_in_source(seg):
    out = []
    for tok in tokenize.generate_tokens(io.StringIO("(" + seg + ")").readline):
        if tok.type != tokenize.STRING:
            continue
        s = tok.string
        i = 0
        while s[i] not in "'\"":
            i += 1
        if "f" not in s[:i].lower():
            continue
        q = s[i:i + 3] if s[i:i + 3] in ("'''", '"""') else s[i]
        body = s[i + len(q):len(s) - len(q)]
        j = 0
        while j < len(body):
            c = body[j]
            if body[j:j + 2] in ("{{", "}}"):
                j += 2
                continue
            if c == "{":
                k, depth, quote = j + 1, 1, None
                while k < len(body):
                    d = body[k]
                    if quote:
                        if d == quote:
                            quote = None
                    elif d in "'\"":
                        quote = d
                    elif d in "{[(":
                        depth += 1
                    elif d in "}])":
                        depth -= 1
                        if depth == 0 and d == "}":
                            break
                    k += 1
                out.append(body[j:k + 1])
                j = k + 1
                continue
            j += 1
    return out


def _escape(s, q):
    s = s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return s.replace(q, "\\" + q) if len(q) == 1 else s


def build_fstring(template, fields):
    parts, order, pos = [], [], 0
    for m in FIELD.finditer(template):
        parts += [("lit", template[pos:m.start()]), ("field", int(m.group(1)))]
        order.append(int(m.group(1)))
        pos = m.end()
    parts.append(("lit", template[pos:]))
    if order != list(range(len(fields))):
        raise ValueError(f"fields {order} must be 0..{len(fields) - 1} in order")
    joined = "".join(fields)
    q = next((x for x in ('"', "'", '"""', "'''") if x not in joined), None)
    if q is None:
        raise ValueError("no free quote character")
    body = ""
    for kind, v in parts:
        if kind == "lit":
            lit = v.replace("{{", "\x00").replace("}}", "\x01")
            if "{" in lit or "}" in lit:
                raise ValueError(f"single brace in translation: {v!r}")
            body += _escape(lit, q).replace("\x00", "{{").replace("\x01", "}}")
        else:
            body += fields[v]
    if len(q) == 3 and body.endswith(q[0]):
        body = body[:-1] + "\\" + q[0]
    return "f" + q + body + q


def _check_translation(e, cfg):
    t = e["translation"]
    lit = FIELD.sub("X", t).replace("{{", "{").replace("}}", "}") if e["kind"] == "fstr" else t
    if not _free(lit, cfg):
        raise ValueError(f"{e['file']}: translation has fewer than {cfg['text']['min_words']} words: {t!r}")
    if e["kind"] == "json" and NUMBER.findall(t) != NUMBER.findall(e["text"]):
        raise ValueError(f"{e['file']} {e['pointer']}: translation changes the numbers: {t!r}")


def _rename_keys(o, back):
    fwd = {v: k for k, v in back.items()}
    if isinstance(o, dict):
        return {fwd.get(k, k): _rename_keys(v, back) for k, v in o.items()}
    if isinstance(o, list):
        return [_rename_keys(v, back) for v in o]
    return o


def apply(cfg, entries_path=None, mapping_path=None):
    entries_path = entries_path or cfg.folder("translation") / "literals.json"
    entries = read_json(entries_path, [])
    if mapping_path:
        mapping = read_json(mapping_path, {})
        for e in entries:
            if not e.get("translation") and e["text"] in mapping:
                e["translation"] = mapping[e["text"]]
    todo = [e for e in entries if e.get("translation") and e["translation"] != e["text"]]
    for e in todo:
        _check_translation(e, cfg)
    by_file = {}
    for e in todo:
        by_file.setdefault(e["file"], []).append(e)
    log = []
    for rel in sorted(set(by_file) | set(cfg["payload"]["key_translations"])):
        p = cfg.repo / rel
        if not p.exists():
            continue
        items = by_file.get(rel, [])
        src = p.read_text(encoding="utf-8")
        if rel.endswith(".py"):
            tree = ast.parse(src)
            inside_f = {id(c) for x in ast.walk(tree) if isinstance(x, ast.JoinedStr) for c in ast.walk(x) if c is not x}
            nodes = {}
            for x in ast.walk(tree):
                if isinstance(x, (ast.Constant, ast.JoinedStr)) and hasattr(x, "lineno") and id(x) not in inside_f:
                    nodes.setdefault((x.lineno, x.col_offset), []).append(x)
            targets = []
            for e in items:
                cand = [x for x in nodes.get((e["line"], e["col"]), [])
                        if (e["kind"] == "str" and isinstance(x, ast.Constant) and x.value == e["text"])
                        or (e["kind"] == "fstr" and isinstance(x, ast.JoinedStr))]
                if len(cand) != 1:
                    raise ValueError(f"{rel}:{e['line']}: literal not found; re-run extract")
                targets.append((cand[0], e))
            lines = src.split("\n")
            for n, e in sorted(targets, key=lambda t: (t[0].lineno, t[0].col_offset), reverse=True):
                head, mid, tail = segment(lines, n)
                if e["kind"] == "str":
                    new = repr(e["translation"])
                else:
                    fields = fields_in_source(mid)
                    if len(fields) != e["fields"]:
                        raise ValueError(f"{rel}:{e['line']}: f-string fields changed; re-run extract")
                    new = build_fstring(e["translation"], fields)
                lines[n.lineno - 1:n.end_lineno] = [head + new + tail]
                log.append({"file": rel, "line": n.lineno, "before": e["text"], "after": e["translation"]})
            new_src = "\n".join(lines)
            ast.parse(new_src)
        elif rel.endswith(".json"):
            obj = json.loads(src)
            for e in items:
                o = obj
                for k in e["pointer"][:-1]:
                    o = o[k]
                if o[e["pointer"][-1]] != e["text"]:
                    raise ValueError(f"{rel} {e['pointer']}: value changed; re-run extract")
                o[e["pointer"][-1]] = e["translation"]
                log.append({"file": rel, "pointer": e["pointer"], "before": e["text"], "after": e["translation"]})
            back = cfg["payload"]["key_translations"].get(rel)
            if back:
                obj = _rename_keys(obj, back)
                log.append({"file": rel, "keys_translated": sorted(back)})
            indent = 2 if "\n  \"" in src else (1 if "\n \"" in src else None)
            new_src = json.dumps(obj, indent=indent, ensure_ascii=False) + ("\n" if src.endswith("\n") else "")
        else:
            continue
        if new_src != src:
            p.write_text(new_src, encoding="utf-8")
    write_json(cfg.ledger / "translate.json", log)
    return log
