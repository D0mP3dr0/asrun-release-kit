"""Project configuration: loading, defaults and the template written by ``asrun init``."""
import copy
import pathlib

import yaml

DEFAULT_FORBIDDEN_TERMS = [  # asrun: allow
    "confidential", "do not publish", "do-not-publish", "password", "api_key", "secret_key", "private_key",  # asrun: allow
]  # asrun: allow

DEFAULT_FOREIGN_WORDS = [
    "não", "nao", "também", "então", "uma", "pelo", "pela", "você", "isso", "aqui", "ainda", "pode", "deve",
    "porque", "quando", "onde", "mais", "está", "são", "com", "sem", "para", "dos", "das", "nos", "arquivo",
    "los", "las", "del", "una", "pero", "muy", "esta", "desde", "hasta", "avec", "dans", "pour", "sont", "nous",
    "und", "nicht", "mit", "ist", "oder", "auch", "wird", "sind",
]

DEFAULTS = {
    "project": "my-study",
    "sources": {},
    "layout": [],
    "workdir": {"original": "original", "repo": "repo", "ledger": "ledger", "translation": "translation"},
    "paths": {"roots": [], "internal_names": [], "neutral_folder": "internal", "markers": [], "rename_identifiers": {}},
    "text": {"min_words": 4},
    "payload": {"key_translations": {}, "readme": "README.md", "readme_claims": []},
    "publish": {"new_files": {}, "declared_changes": {}, "keep_empty_dirs": []},
    "scan": {"forbidden_terms": DEFAULT_FORBIDDEN_TERMS, "max_file_mb": 10, "foreign_words": DEFAULT_FOREIGN_WORDS,
             "allow_emails": [], "exclude": [".git/*", "*/__pycache__/*"]},
}

TEMPLATE = """\
# asrun project configuration. Paths are relative to this file unless absolute.
project: {project}

# Read-only trees with the files exactly as run. Never written to.
sources:
  main: ../as_run

# Published layout: each entry copies one file (or a glob) from a source into the release.
# A glob keeps the structure below its fixed part; 'dst' is then a folder.
layout:
  - {{src: "main:train.py", dst: "train.py", why: "entry point"}}
  - {{src: "main:results/*.json", dst: "results/", why: "records quoted in the README"}}

# Working folders (original/ keeps byte copies, repo/ becomes the public tree).
workdir: {{original: original, repo: repo, ledger: ledger, translation: translation}}

paths:
  # Absolute prefixes hard-coded in the code as run. In Python they become a variable
  # INTERNAL_<var> read from the environment variable <env>; elsewhere the text ${{<env>}}.
  roots:
    - {{prefix: "/abs/prefix/of/the/lab", var: DATA_ROOT, env: PROJECT_DATA_ROOT}}
  # Folder or file names that must not appear; replaced by 'neutral_folder'.
  internal_names: []
  neutral_folder: internal
  # Local values replaced by a placeholder text everywhere.
  markers: []        # - {{value: "Some Local Name", marker: "${{SOURCE_NAME}}"}}
  # Identifiers renamed to a neutral name (must start with INTERNAL_).
  rename_identifiers: {{}}

text:
  min_words: 4       # strings with this many words count as free text

payload:
  key_translations: {{}}   # {{results/x.json: {{published_key: key_as_written}}}}
  readme: README.md
  readme_claims: []        # - {{quote: "0.81 accuracy", file: results/m.json, path: [accuracy], value: 0.81}}

publish:
  new_files: {{}}          # {{README.md: publish/README.md}} files written for the release
  declared_changes: {{}}   # {{docs/notes.md: "translated into English"}}
  keep_empty_dirs: []

scan:
  # forbidden_terms replaces the default list; keep the defaults and add the terms that name
  # the inside of your lab (folder names, local names, process words).
  # forbidden_terms: [...]
  max_file_mb: 10
  allow_emails: []
  exclude: [".git/*"]
"""


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path):
        self.path = pathlib.Path(path).resolve()
        self.base = self.path.parent
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.data = _merge(DEFAULTS, raw)
        self.validate()

    def __getitem__(self, k):
        return self.data[k]

    def where(self, rel):
        p = pathlib.Path(rel)
        return p if p.is_absolute() else (self.base / p).resolve()

    def folder(self, key):
        return self.where(self.data["workdir"][key])

    @property
    def original(self):
        return self.folder("original")

    @property
    def repo(self):
        return self.folder("repo")

    @property
    def ledger(self):
        return self.folder("ledger")

    def source_root(self, key):
        return self.where(self.data["sources"][key])

    def validate(self):
        p = self.data["paths"]
        for r in p["roots"]:
            for k in ("prefix", "var", "env"):
                if k not in r:
                    raise SystemExit(f"paths.roots entry needs '{k}': {r}")
            if not r["var"].isidentifier():
                raise SystemExit(f"paths.roots var is not an identifier: {r['var']}")
        for old, new in p["rename_identifiers"].items():
            if not new.startswith("INTERNAL_") or not new.isidentifier():
                raise SystemExit(f"rename_identifiers: {old} -> {new} must start with INTERNAL_")
        for m in p["markers"]:
            if not m.get("value") or not m.get("marker"):
                raise SystemExit(f"paths.markers entry needs value and marker: {m}")


def write_template(path, project):
    path = pathlib.Path(path)
    if path.exists():
        raise SystemExit(f"{path} exists; not overwritten")
    path.write_text(TEMPLATE.format(project=project), encoding="utf-8")
    return path
