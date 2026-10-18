"""Check that the published sources contain the same code as the sources as run.

Before publication the Python sources were cleaned: comments and docstrings were removed,
free-text string literals (MIN_WORDS or more words: log, console and help messages) were
translated, absolute data-root prefixes were replaced by a variable INTERNAL_<NAME> read from
the environment in one added statement, and some local values were replaced by neutral
placeholders (listed in PROVENANCE.md).

For each file listed in CODE_AST_SHA256 this script computes the sha256 of its syntax tree
with exactly these parts normalized:
  - docstrings are dropped;
  - the added module-level statement INTERNAL_<NAME> = __import__("os").environ.get(..., ...)
    is dropped;
  - an f-string that starts with {INTERNAL_<NAME>} has that field replaced by the text <NAME>
    in angle brackets, and an f-string with no interpolated expression counts as a plain string;
  - a plain string with MIN_WORDS or more words becomes a fixed placeholder; an f-string whose
    literal text meets the same test keeps only its interpolated expressions, in order;
  - a variable whose name starts with INTERNAL_ becomes one fixed name.
Everything else (names, calls, operators, numbers, short strings such as keys, labels and
relative paths, and every expression inside an f-string) enters the digest, so any change to
executable code fails the check. YAML files listed in CODE_AST_SHA256 are checked on their
parsed content (comments are not content), with the same free-text rule; this needs PyYAML.

CODE_AST_SHA256 lists the digest of each file as run, computed before publication. The layout
of the syntax tree can differ between Python versions: run this with the version named in the
first line of CODE_AST_SHA256.

Usage, from the repository root:  python3 verify_code.py
"""
import ast
import hashlib
import json
import pathlib
import sys

CONFIG = {"min_words": 4, "neutral_prefix": "INTERNAL_", "neutral_name": "INTERNAL_NAME", "placeholder": "<text>"}


def is_free_text(s):
    return len(s.split()) >= CONFIG["min_words"]


def _is_neutral_name(node):
    return isinstance(node, ast.Name) and node.id.startswith(CONFIG["neutral_prefix"])


def is_env_statement(stmt):
    if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and _is_neutral_name(stmt.targets[0])):
        return False
    v = stmt.value
    return (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "get"
            and isinstance(v.func.value, ast.Attribute) and v.func.value.attr == "environ"
            and isinstance(v.func.value.value, ast.Call) and isinstance(v.func.value.value.func, ast.Name)
            and v.func.value.value.func.id == "__import__"
            and len(v.args) == 2 and all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in v.args))


def drop_docstrings_and_env(tree):
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            b = n.body[0]
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                n.body = n.body[1:] or [ast.Pass()]
    tree.body = [s for s in tree.body if not is_env_statement(s)] or [ast.Pass()]
    return tree


def root_token(name):
    return "<" + name[len(CONFIG["neutral_prefix"]):] + ">"


class RootField(ast.NodeTransformer):
    def visit_JoinedStr(self, node):
        self.generic_visit(node)
        vals = list(node.values)
        if vals and isinstance(vals[0], ast.FormattedValue) and _is_neutral_name(vals[0].value) \
                and vals[0].conversion == -1 and vals[0].format_spec is None:
            token = root_token(vals[0].value.id)
            vals = vals[1:]
            if vals and isinstance(vals[0], ast.Constant):
                vals[0] = ast.Constant(value=token + vals[0].value)
            else:
                vals.insert(0, ast.Constant(value=token))
        node.values = vals
        if all(isinstance(v, ast.Constant) for v in vals):
            return ast.Constant(value="".join(v.value for v in vals))
        return node


class Mask(ast.NodeTransformer):
    def visit_JoinedStr(self, node):
        text = "".join(v.value if isinstance(v, ast.Constant) else "X" for v in node.values)
        if is_free_text(text):
            node.values = [v for v in node.values if not isinstance(v, ast.Constant)]
        return self.generic_visit(node)

    def visit_FormattedValue(self, node):
        node.value = self.visit(node.value)
        if node.format_spec is not None:
            node.format_spec = self.visit(node.format_spec)
        return node

    def visit_Name(self, node):
        if node.id.startswith(CONFIG["neutral_prefix"]):
            node.id = CONFIG["neutral_name"]
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, str) and is_free_text(node.value):
            return ast.Constant(value=CONFIG["placeholder"])
        return node


def normalized_tree(src, before=()):
    tree = drop_docstrings_and_env(ast.parse(src))
    for t in before:
        tree = t.visit(tree)
    tree = RootField().visit(tree)
    return Mask().visit(tree)


def code_sha256(path, before=()):
    tree = normalized_tree(pathlib.Path(path).read_text(encoding="utf-8"), before)
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest()


def yaml_norm(o, text=lambda s: s):
    if isinstance(o, dict):
        return {str(yaml_norm(k, text)): yaml_norm(v, text) for k, v in o.items()}
    if isinstance(o, list):
        return [yaml_norm(v, text) for v in o]
    if isinstance(o, str):
        s = text(o)
        return CONFIG["placeholder"] if is_free_text(s) else s
    return o


def yaml_sha256(path, text=lambda s: s):
    import yaml
    doc = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    canon = json.dumps(yaml_norm(doc, text), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def file_sha256(path):
    return yaml_sha256(path) if str(path).endswith((".yaml", ".yml")) else code_sha256(path)


def read_digests(path):
    version, expected = None, {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("# python "):
            version = line.split()[2]
        elif line.strip() and not line.startswith("#"):
            digest, rel = line.split(None, 1)
            expected[rel.strip()] = digest
    return version, expected


def main():
    root = pathlib.Path(__file__).resolve().parent
    version, expected = read_digests(root / "CODE_AST_SHA256")
    here = "%d.%d" % sys.version_info[:2]
    if version and version != here:
        print(f"warning: digests were computed with Python {version}; this is Python {here}")
    bad = 0
    for rel, digest in sorted(expected.items()):
        ok = file_sha256(root / rel) == digest
        bad += not ok
        if not ok:
            print(f"MISMATCH {rel}")
    print(f"{len(expected) - bad}/{len(expected)} files: code identical to the code as run")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
