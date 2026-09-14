#!/bin/bash
# test.sh — backend tests (no external integration)
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# shellcheck source=/dev/null
source "$PROJECT_ROOT/scripts/lib/gate-slots.sh"
nadili_gate_run_register
trap nadili_gate_run_unregister EXIT
# shellcheck source=/dev/null
source "$PROJECT_ROOT/venv/bin/activate"

echo "=== Backend tests ==="
pytest_jobs="$(nadili_gate_pytest_jobs)"
nice_prefix_value="$(nadili_gate_nice_prefix)"
pytest_start_seconds=$SECONDS
set +e
pytest_command=(pytest apps/api/tests tests/unit -m "not integration" -v --tb=short -n "$pytest_jobs" --dist worksteal --durations=25)
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
