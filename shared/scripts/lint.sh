#!/bin/bash
# lint.sh — backend static checks. Use scripts/pre-commit.sh for the full gate.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# shellcheck source=/dev/null
source "$PROJECT_ROOT/venv/bin/activate"

hr()  { echo ""; echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; }
ok()  { echo "  ✓  $1"; }
info(){ echo "  →  $1"; }

hr
echo "  nadili lint  (auto-fix mode)"
hr

PY_FILES=$(find apps/api/ nadili_runtime/ -name "*.py" | wc -l | tr -d ' ')

echo ""
echo "[ 1/3 ]  Ruff lint — auto-fix"
echo ""
info "Scanning $PY_FILES files in apps/api/, nadili_runtime/"
echo ""
ruff check apps/api/ nadili_runtime/ scripts/check_boundaries.py scripts/changed_areas.py --fix
ok "Lint clean"

echo ""
echo "[ 2/3 ]  Ruff format"
echo ""
ruff format apps/api/ nadili_runtime/ scripts/check_boundaries.py scripts/changed_areas.py
ok "Formatting applied"

echo ""
echo "[ 3/3 ]  Mypy — type check apps/api/"
echo ""
info "Strict backend mode"
echo ""
mypy apps/api/ --strict --ignore-missing-imports --exclude '/tests/'
ok "No type errors"

hr
echo ""
echo "  ✓  Lint done — run scripts/pre-commit.sh before committing"
echo ""
