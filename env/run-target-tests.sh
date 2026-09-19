#!/usr/bin/env bash
# Usage: env/run-target-tests.sh <path-to-mlem-tree> [v1|v2]
#   v1 = baseline env (pydantic 1.10).  v2 = same env with the pydantic-2 overlay in front.
# Runs the Pydantic-heavy test dirs, skipping the 21 known environmental failures.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -W 2>/dev/null || pwd)"
TREE="$(cd "${1:?tree path required}" && pwd -W 2>/dev/null || pwd)"; MODE="${2:-v1}"
DESEL=$(sed 's/^/--deselect=/' "$ROOT/env/baseline-known-failures.txt" | tr '\n' ' ')
if [ "$MODE" = "v2" ]; then export PYTHONPATH="$TREE;$ROOT/.overlay-pydantic2"; else export PYTHONPATH="$TREE"; fi
cd "$TREE"
"$ROOT/.venv-target/Scripts/python.exe" -m pytest tests/polydantic tests/core tests/utils tests/test_config.py \
  -p no:cacheprovider -o addopts="" -q --timeout=90 \
  -m "not long and not docker and not kubernetes and not conda" $DESEL "${@:3}"
