"""Check that the published records carry the same numbers as the records as written, and that
every number quoted in the README is the number in its record.

Before publication, free-text notes of the JSON and CSV records (MIN_WORDS or more words) were
translated, some keys were translated (KEY_TRANSLATIONS maps each published key back to the key
as written), and some local values were replaced by neutral placeholders (listed in
PROVENANCE.md).

For each file listed in PAYLOAD_SHA256 this script computes the sha256 of its payload:
  - JSON: the canonical JSON with each key mapped back to the key as written and each string of
    MIN_WORDS or more words replaced by the list of the numbers it contains, in order (so a
    translation must keep them); every other string, number, boolean, null and the whole
    structure enter the digest;
  - CSV: the number of rows and of cells per row and, for each cell, the list of the numbers it
    contains, in order.
PAYLOAD_SHA256 lists the digest of each record as written, computed before publication.

The script then checks each entry of README_CLAIMS: the quoted text must appear in the README
and the value at the given place of the record must equal the quoted number.

Usage, from the repository root:  python3 verify_payload.py
"""
import csv
import hashlib
import io
import json
import pathlib
import re
import sys

CONFIG = {"min_words": 4, "key_translations": {}, "readme": "README.md", "readme_claims": []}
NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def is_free_text(s):
    return len(s.split()) >= CONFIG["min_words"]


def json_norm(o, keymap, text=lambda s: s):
    if isinstance(o, dict):
        return {text(keymap.get(k, k)): json_norm(v, keymap, text) for k, v in o.items()}
    if isinstance(o, list):
        return [json_norm(v, keymap, text) for v in o]
    if isinstance(o, str):
        s = text(o)
        return {"<text>": NUMBER.findall(s)} if is_free_text(s) else s
    return o


def json_payload(obj, keymap, text=lambda s: s):
    canon = json.dumps(json_norm(obj, keymap, text), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def csv_payload(src, text=lambda s: s):
    lines = src.splitlines() or [""]
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    rows = list(csv.reader(io.StringIO(src), delimiter=delim))
    canon = json.dumps([[NUMBER.findall(text(c)) for c in r] for r in rows], separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def payload_sha256(path, rel, text=lambda s: s, keymap=None):
    src = pathlib.Path(path).read_text(encoding="utf-8-sig")
    if rel.endswith(".csv"):
        return csv_payload(src, text)
    if keymap is None:
        keymap = CONFIG["key_translations"].get(rel, {})
    return json_payload(json.loads(src), keymap, text)


def check_claims(base):
    readme = (base / CONFIG["readme"]).read_text(encoding="utf-8") if (base / CONFIG["readme"]).exists() else ""
    bad = 0
    for claim in CONFIG["readme_claims"]:
        o = json.loads((base / claim["file"]).read_text(encoding="utf-8"))
        try:
            for k in claim["path"]:
                o = o[k]
        except (KeyError, IndexError, TypeError):
            o = None
        ok = claim["quote"] in readme and o == claim["value"] and type(o) is not bool
        bad += not ok
        if not ok:
            print(f"CLAIM FAILS: {claim['quote']!r} vs {claim['file']} {claim['path']} = {o!r}")
    return bad


def main():
    base = pathlib.Path(__file__).resolve().parent
    expected = {}
    for line in (base / "PAYLOAD_SHA256").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, rel = line.split(None, 1)
            expected[rel.strip()] = digest
    bad = 0
    for rel, digest in sorted(expected.items()):
        ok = payload_sha256(base / rel, rel) == digest
        bad += not ok
        if not ok:
            print(f"MISMATCH {rel}")
    print(f"{len(expected) - bad}/{len(expected)} records: payload identical to the record as written")
    bad_claims = check_claims(base)
    n = len(CONFIG["readme_claims"])
    print(f"{n - bad_claims}/{n} README numbers found in their records")
    sys.exit(1 if bad or bad_claims else 0)


if __name__ == "__main__":
    main()
