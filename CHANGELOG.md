# Changelog

## 0.1.0 (2026-10-18)

First release.

- Commands `init`, `assemble`, `strip`, `neutralize-paths`, `translate` (extract and apply),
  `scan`, `finalize`, `check` and `rewrite-history`.
- Generated, self-sufficient verifiers `verify_code.py` (syntax-tree digests of Python and YAML)
  and `verify_payload.py` (payload digests of JSON and CSV, README number checks).
- Synthetic example under `examples/tiny-study/` and a unittest suite with tampering tests.
