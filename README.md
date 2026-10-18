# asrun

Publish research code as it ran, with proofs that the code and the numbers did not change on the way.

## The problem

A paper reports numbers. The code behind them ran on a lab machine with hard-coded absolute paths,
comments in the authors' first language, notes about internal folders and log messages nobody
outside the lab can read. Before publication, someone cleans it. After the cleaning, two questions
have no answer unless the release carries the evidence:

1. **Is the published code the code that ran?** Removing comments, translating messages and
   replacing paths touches every file. A slip in that pass (a deleted line, a changed constant, a
   "small fix") is invisible in a diff against nothing.
2. **Is the number in the README the number in the record?** Numbers get copied by hand from logs
   and records into prose, and copies drift.

asrun answers both with digests that a reader recomputes from the published files alone.
`verify_code.py` recomputes, for every Python and YAML file, a digest of its syntax tree in which
only the parts the cleaning was allowed to change are normalized, and compares it with the digest
of the file as run. `verify_payload.py` does the same for JSON and CSV records (structure, keys,
values and the numbers inside translated notes) and checks every README number listed in its
configuration against the record it comes from. Both verifiers are generated, use only the Python
standard library (plus PyYAML for YAML files) and run from the repository root.

## The flow

```
asrun init                 # write asrun.yaml: sources as run, published layout, roots, terms
asrun assemble             # original/ (byte copies) and repo/ (to clean), ledger with source sha256
asrun strip                # remove comments and docstrings; the syntax tree must stay identical
asrun neutralize-paths     # absolute roots -> INTERNAL_<VAR> from the environment; internal names,
                           # local values -> neutral text in Python, YAML, JSON and CSV
asrun translate --extract  # list free-text literals and notes for a translator
asrun translate --apply    # apply the translations; fields of f-strings and numbers must be kept
asrun scan                 # forbidden terms, absolute paths, e-mails, non-English comments,
                           # author metadata in xlsx/docx/pptx/pdf/png, large files
asrun finalize             # verifiers, CODE_AST_SHA256, PAYLOAD_SHA256, PROVENANCE.md, SHA256SUMS
asrun check                # SHA256SUMS, run both verifiers, self-sufficiency and leak checks
asrun rewrite-history ...  # write (never run) a single-commit, force-push, tag and release script
```

Every step after `assemble` writes only inside `repo/` and its ledger folder, and every step that
edits code compares the result with `original/` under the published rules before it returns.
`finalize` refuses to write the digests if any code or payload file differs, or if any other file
differs in bytes without a declared change.

`examples/tiny-study/` holds a synthetic study whose `as_run/` tree is dirty on purpose (each trap
is marked in a comment) and the configuration that turns it into a clean release:

```
bash examples/tiny-study/run_example.sh          # PYTHON=... to choose the interpreter
```

The release appears in `examples/tiny-study/release/build/repo/`. The tests run the same flow on a
copy and then tamper with it:

```
python3 -m unittest discover -s tests
```

## What the digests guarantee, and what they ignore

A code digest covers every name, call, operator, number, keyword, control-flow statement and every
short string (keys, labels, relative paths, column names), and every expression inside an f-string.
Changing any of them in the published file gives `MISMATCH`. The digest deliberately ignores:

- comments and docstrings;
- the content of free-text strings, that is strings with `min_words` or more words (4 by default):
  log, console and help messages, which are what gets translated. Rewording such a message, in any
  language, passes the check, and so would hiding a meaningful long string inside one;
- the names of variables that start with `INTERNAL_`, which all collapse to one name (swapping two
  such variables is not detected), and the one added statement that reads each root variable from
  the environment;
- an `{INTERNAL_<VAR>}` field at the start of an f-string, which stands for the root prefix;
- in YAML files, comments and formatting (the parsed content is compared).

A payload digest covers the structure, every key (translated keys are mapped back), every value of
JSON records and the numbers of every free-text note, in order. It ignores the wording of notes,
JSON formatting and key order and, in CSV files, everything in a cell except its numbers.

Other limits:

- The digests prove that the published files equal the files in `original/`, which are byte copies
  of the sources named in the configuration. Whether those sources are the ones that produced the
  reported results is the author's statement; record it in the README or in `PROVENANCE.md`.
- Syntax trees differ between Python versions. `CODE_AST_SHA256` records the version used, and
  `verify_code.py` warns when another one runs it.
- Only the README numbers listed in `payload.readme_claims` are checked.
- `scan` lists findings and changes nothing; author metadata in binary files is reported, not
  removed. The non-English test is a simple heuristic. A line carrying the text `asrun: allow`
  is skipped by the term, path and e-mail checks (for the configured lists themselves).
- `strip` refuses a docstring that shares a line with code, and `neutralize-paths` asks for a
  manual edit when a root prefix with backslashes starts an f-string.
- `rewrite-history` writes a script that rewrites public history with `--force`. It never runs it.

## Install

```
pip install .            # Python 3.10 or later; depends on PyYAML
asrun --help
```

## License

MIT; see `LICENSE`. To cite, see `CITATION.cff`.
