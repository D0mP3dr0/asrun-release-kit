#!/usr/bin/env bash
# The whole flow on the synthetic example. Run from the repository root:
#   bash examples/tiny-study/run_example.sh
# It writes only under examples/tiny-study/release/build/ (removed first).
set -euo pipefail
PY=${PYTHON:-python3}
HERE=$(cd "$(dirname "$0")" && pwd)
CFG="$HERE/release/asrun.yaml"
rm -rf "$HERE/release/build"

"$PY" -m asrun -c "$CFG" assemble
"$PY" -m asrun -c "$CFG" strip
"$PY" -m asrun -c "$CFG" neutralize-paths
"$PY" -m asrun -c "$CFG" translate --extract
"$PY" -m asrun -c "$CFG" translate --apply --map "$HERE/release/translations.json"
"$PY" -m asrun -c "$CFG" scan
"$PY" -m asrun -c "$CFG" finalize
"$PY" -m asrun -c "$CFG" check
echo "release tree: $HERE/release/build/repo"
