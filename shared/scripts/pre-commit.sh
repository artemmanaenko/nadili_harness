#!/usr/bin/env bash
# Full commit quality gate for the backend and both web clients.
set -euo pipefail
umask 077

if [[ "${1:-full}" != "full" ]]; then
  printf 'Usage: bash scripts/pre-commit.sh\n' >&2
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# shellcheck source=/dev/null
source "$root/scripts/lib/gate-slots.sh"
# shellcheck source=/dev/null
source "$root/scripts/lib/mypy-cache.sh"
nadili_gate_run_register
cleanup() {
  nadili_gate_mypy_cache_release
  nadili_gate_run_unregister
}
trap cleanup EXIT
source "$root/venv/bin/activate"

printf 'nadili full pre-commit gate\n'
ruff check apps/api/ apps/pdf_parser/ core/pdf_documents.py nadili_runtime/ scripts/check_boundaries.py scripts/changed_areas.py
ruff format --check apps/api/ apps/pdf_parser/ core/pdf_documents.py nadili_runtime/ scripts/check_boundaries.py scripts/changed_areas.py
ruff check scripts/ tests/integration/ tests/unit/
ruff format --check scripts/ tests/integration/ tests/unit/
# The standalone Python client owns its own toolchain (isolated PYTHONPATH, strict mypy, no
# backend imports); scripts/client.sh is that capability's single runner.
bash scripts/client.sh
mypy_cache_dir="$(nadili_gate_mypy_cache_dir "$root" "$root/venv/bin/python")"
nadili_gate_mypy_cache_acquire "$mypy_cache_dir"
mypy_cache_dir="$nadili_mypy_cache_dir"
mypy apps/api/ apps/pdf_parser/ core/pdf_documents.py --strict --ignore-missing-imports --exclude '/tests/' --cache-dir "$mypy_cache_dir"
mypy -p scripts --strict --ignore-missing-imports --cache-dir "$mypy_cache_dir"
mypy tests/unit/ --strict --ignore-missing-imports --cache-dir "$mypy_cache_dir"
mypy tests/integration/ --strict --ignore-missing-imports --cache-dir "$mypy_cache_dir"
nadili_gate_mypy_cache_release
printf 'pytest suites: apps/api/tests tests/unit\n'
# Default xdist workers derive from cores and live sibling gates; NADILI_PYTEST_JOBS overrides it.
pytest_jobs="$(nadili_gate_pytest_jobs)"
nice_prefix_value="$(nadili_gate_nice_prefix)"
pytest_start_seconds=$SECONDS
set +e
pytest_command=(python -m pytest apps/api/tests tests/unit -m "not integration" -q -n "$pytest_jobs" \
  --dist worksteal --durations=25)
if [[ -n "$nice_prefix_value" ]]; then
  nice_prefix=()
  read -r -a nice_prefix <<< "$nice_prefix_value"
  pytest_command=("${nice_prefix[@]}" "${pytest_command[@]}")
fi
"${pytest_command[@]}"
pytest_status=$?
set -e
printf 'unit tests: %ss\n' "$((SECONDS - pytest_start_seconds))"
if (( pytest_status != 0 )); then
  exit "$pytest_status"
fi
python scripts/check_boundaries.py
python scripts/check_work_item_scope.py
python scripts/check_plan_interleaving.py
python scripts/check_hook_install.py
python scripts/check_design_tokens.py
python scripts/check_staged_documents.py
python scripts/check_secret_catalog.py
bash scripts/check-host-bootstrap.sh
bash scripts/web-check.sh full
printf 'Full checks passed.\n'
