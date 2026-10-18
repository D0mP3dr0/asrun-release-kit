"""List what should not be published: forbidden terms, absolute paths, e-mail addresses,
non-English comments, author metadata in office, PDF and PNG files, and large files.
The scan lists; it does not change anything. A line carrying the text 'asrun: allow' is
skipped by the term and path checks (for the lists of terms and prefixes themselves)."""
import ast
import fnmatch
import io
import re
import struct
import tokenize
import zipfile
import zlib

from .lang import looks_non_english

ALLOW = "asrun: allow"
ABS_PATH = re.compile(
    r"(?<![\w.~$])/(?:home|Users|root|mnt|media|srv|opt|scratch|Volumes|content/drive|var/folders|private/var)/[\w.-]"
    r"|(?<![\w])[A-Za-z]:[\\/]{1,2}(?![ntr](?!\w))[\w.$-]")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
BINARY = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".xlsx", ".docx", ".pptx", ".zip", ".gz", ".npz", ".npy",
          ".pt", ".pth", ".pkl", ".h5", ".tif", ".tiff", ".parquet", ".whl")
OFFICE = (".xlsx", ".docx", ".pptx")
HASH_COMMENT = (".yaml", ".yml", ".sh", ".toml", ".cfg", ".ini", ".r", ".R")
OFFICE_FIELDS = re.compile(r"<(?:\w+:)?(creator|lastModifiedBy|Company|Manager|Author)\b[^>]*>([^<]+)<")
PDF_FIELDS = re.compile(rb"/(Author|Creator)\s*\(([^)]{1,200})\)")
XMP_CREATOR = re.compile(rb"<dc:creator>(.{0,400}?)</dc:creator>", re.S)


def _terms_regex(terms):
    if not terms:
        return None
    alts = sorted({re.escape(t) for t in terms}, key=len, reverse=True)
    return re.compile(r"(?<![\w])(?:" + "|".join(alts) + r")(?![\w])", re.I)


def _excluded(rel, patterns):
    return any(fnmatch.fnmatch(rel, pat) or rel.startswith(pat.rstrip("*").rstrip("/") + "/") and pat.endswith("/*")
               for pat in patterns)


def py_comments(src):
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT and not tok.string.startswith("#!"):
                out.append((tok.start[0], "comment", tok.string.lstrip("# ")))
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                d = ast.get_docstring(n, clean=False)
                if d:
                    out.append((getattr(n, "lineno", 1), "docstring", d))
    except (SyntaxError, tokenize.TokenError) as e:
        out.append((0, "unreadable", repr(e)))
    return out


def hash_comments(src):
    return [(i, "comment", ln.strip().lstrip("# ")) for i, ln in enumerate(src.splitlines(), 1)
            if ln.strip().startswith("#") and not ln.strip().startswith("#!")]


def png_text(data):
    out = []
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return out
    pos = 8
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"tEXt":
            k, _, v = body.partition(b"\x00")
            out.append((k.decode("latin-1"), v.decode("latin-1")[:120]))
        elif kind == b"zTXt":
            k, _, v = body.partition(b"\x00")
            try:
                v = zlib.decompress(v[1:])
            except zlib.error:
                v = b"?"
            out.append((k.decode("latin-1"), v.decode("latin-1", "replace")[:120]))
        elif kind == b"iTXt":
            k, _, v = body.partition(b"\x00")
            out.append((k.decode("latin-1"), v[2:].split(b"\x00", 2)[-1].decode("utf-8", "replace")[:120]))
        elif kind == b"eXIf":
            out.append(("eXIf", f"{n} bytes"))
        elif kind == b"IEND":
            break
    return out


def office_meta(path):
    out = []
    with zipfile.ZipFile(path) as z:
        for name in ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml"):
            if name in z.namelist():
                t = z.read(name).decode("utf-8", "replace")
                out += [(f"{name}:{k}", v.strip()) for k, v in OFFICE_FIELDS.findall(t) if v.strip()]
    return out


def pdf_meta(data):
    out = [(k.decode(), v.decode("latin-1", "replace")) for k, v in PDF_FIELDS.findall(data)]
    out += [("xmp:creator", re.sub(rb"<[^>]+>", b" ", v).decode("utf-8", "replace").strip()[:120])
            for v in XMP_CREATOR.findall(data)]
    return [(k, v) for k, v in out if v.strip()]


def scan(root, cfg_scan, extra_prefixes=()):
    terms = _terms_regex(cfg_scan["forbidden_terms"])
    allow_emails = {e.lower() for e in cfg_scan.get("allow_emails", [])}
    max_bytes = float(cfg_scan.get("max_file_mb", 10)) * 1024 * 1024
    words = cfg_scan.get("foreign_words", [])
    excl = cfg_scan.get("exclude", [])
    found = {"forbidden_term": [], "absolute_path": [], "email": [], "non_english_comment": [],
             "author_metadata": [], "large_file": [], "excluded": []}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".git/") or "__pycache__" in p.parts:
            continue
        if _excluded(rel, excl):
            found["excluded"].append(rel)
            continue
        if terms and terms.search(rel):
            found["forbidden_term"].append({"file": rel, "line": 0, "match": terms.search(rel).group(0)})
        size = p.stat().st_size
        if size > max_bytes:
            found["large_file"].append({"file": rel, "mb": round(size / 1048576, 2)})
        suf = p.suffix.lower()
        data = p.read_bytes()
        if suf == ".png":
            for k, v in png_text(data):
                found["author_metadata"].append({"file": rel, "field": k, "value": v})
        elif suf in OFFICE:
            try:
                for k, v in office_meta(p):
                    found["author_metadata"].append({"file": rel, "field": k, "value": v})
            except zipfile.BadZipFile:
                found["author_metadata"].append({"file": rel, "field": "unreadable", "value": ""})
        elif suf == ".pdf":
            for k, v in pdf_meta(data):
                found["author_metadata"].append({"file": rel, "field": k, "value": v})
        if suf in BINARY or b"\x00" in data[:4096]:
            continue
        text = data.decode("utf-8", "replace")
        for i, line in enumerate(text.splitlines(), 1):
            if ALLOW in line:
                continue
            if terms:
                for m in terms.finditer(line):
                    found["forbidden_term"].append({"file": rel, "line": i, "match": m.group(0)})
            hits = [m.group(0) for m in ABS_PATH.finditer(line)]
            hits += [x for x in extra_prefixes if x and x in line]
            for h in hits:
                found["absolute_path"].append({"file": rel, "line": i, "match": h})
            for m in EMAIL.finditer(line):
                if m.group(0).lower() not in allow_emails:
                    found["email"].append({"file": rel, "line": i, "match": m.group(0)})
        comments = py_comments(text) if suf == ".py" else hash_comments(text) if suf in HASH_COMMENT else []
        for line, kind, c in comments:
            if looks_non_english(c, words):
                found["non_english_comment"].append({"file": rel, "line": line, "kind": kind, "text": c[:100]})
    found["summary"] = {k: len(v) for k, v in found.items() if k != "excluded"}
    found["clean"] = not any(v for k, v in found["summary"].items())
    return found
