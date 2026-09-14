#!/bin/bash
# Fast commit sanity gate for backend and web feedback.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

explicit_capabilities=false
integration_context=false
web_scope=all
requested_capabilities=()
while (($# > 0)); do
  case "$1" in
    --integration)
      integration_context=true
      shift
      ;;
    --web-scope)
      (($# >= 2)) || {
        printf 'Usage: bash scripts/pre-commit-fast.sh [--integration] [--web-scope all|admin|public] [--capability NAME]...\n' >&2
        exit 2
      }
      case "$2" in
        all|admin|public) web_scope="$2" ;;
        *) printf 'pre-commit-fast: invalid web scope %s\n' "$2" >&2; exit 2 ;;
      esac
      shift 2
      ;;
    --capability)
      (($# >= 2)) || {
        printf 'Usage: bash scripts/pre-commit-fast.sh [--integration] [--capability NAME]...\n' >&2
        exit 2
      }
      case "$2" in
        gate-tooling|python-static|python-unit|web-static|web-unit|web-build|harness)
          requested_capabilities+=("$2")
          explicit_capabilities=true
          shift 2
          ;;
        *)
          printf 'pre-commit-fast: unknown explicit capability %s\n' "$2" >&2
          exit 2
          ;;
      esac
      ;;
    *)
      printf 'Usage: bash scripts/pre-commit-fast.sh [--integration] [--capability NAME]...\n' >&2
      exit 2
      ;;
  esac
done

# shellcheck source=/dev/null
source "$PROJECT_ROOT/scripts/lib/gate-slots.sh"
# shellcheck source=/dev/null
source "$PROJECT_ROOT/scripts/lib/mypy-cache.sh"
# shellcheck source=/dev/null
source "$PROJECT_ROOT/venv/bin/activate"

if [[ "$explicit_capabilities" == false ]]; then
  if git diff --cached --quiet; then
    printf 'pre-commit-fast: no staged changes; stage the intended paths or use scripts/dev-check.sh for dirty-scope checks\n' >&2
    exit 2
  fi
  verified_tree="$(python scripts/gate_evidence.py snapshot --print-tree)"
  staged_paths="$(python scripts/gate_lanes.py --integration --staged --commit --paths-only)"
  if [[ -z "$staged_paths" ]]; then
    printf 'pre-commit-fast: no staged changes; stage the intended paths or use scripts/dev-check.sh for dirty-scope checks\n' >&2
    exit 2
  fi
  python scripts/gate_lanes.py --integration --staged --commit
  web_scope="$(python scripts/gate_lanes.py --integration --staged --commit --web-scope-only)"
  capabilities_output="$(
    python scripts/gate_lanes.py --integration --staged --commit --capabilities-only
  )"
  while IFS= read -r capability; do
    [[ -n "$capability" ]] && requested_capabilities+=("$capability")
  done <<< "$capabilities_output"
fi

echo "nadili fast pre-commit gate"
python scripts/check_work_item_scope.py
python scripts/check_plan_interleaving.py
python scripts/check_hook_install.py
python scripts/check_staged_documents.py
python scripts/check_secret_catalog.py

run_python_static=false
run_python_unit=false
run_harness=false
run_web=false
run_web_build=false
run_gate_tooling=false
for capability in "${requested_capabilities[@]}"; do
  case "$capability" in
    gate-tooling) run_gate_tooling=true ;;
    python-static) run_python_static=true ;;
    python-unit) run_python_unit=true ;;
    harness) run_harness=true ;;
    web-static|web-unit) run_web=true ;;
    web-build) run_web=true; run_web_build=true ;;
    docs) ;;
  esac
done

if [[ "$run_gate_tooling" == false && "$run_python_static" == false && \
      "$run_python_unit" == false && \
      "$run_harness" == false && "$run_web" == false ]]; then
  if [[ "$explicit_capabilities" == false ]]; then
    python scripts/gate_evidence.py snapshot --tree "$verified_tree"
  fi
  echo "Instruction/document checks passed; no product test capability selected"
  exit 0
fi

nadili_gate_run_register
cleanup() {
  nadili_gate_mypy_cache_release
  nadili_gate_run_unregister
}
trap cleanup EXIT

if [[ "$run_python_static" == true ]]; then
  python scripts/gate_evidence.py run --check python-static
fi

if [[ "$run_gate_tooling" == true ]]; then
  gate_tooling_tests=(
    tests/unit/test_changed_areas.py
    tests/unit/test_check_item_ready.py
    tests/unit/test_check_plan_interleaving.py
    tests/unit/test_codex_code_review.py
    tests/unit/test_codex_orchestration_budget.py
    tests/unit/test_dev_check_script.py
    tests/unit/test_gate_executor.py
    tests/unit/test_gate_lanes.py
    tests/unit/test_gate_evidence.py
    tests/unit/test_gate_slots_lib.py
    tests/unit/test_gate_test_targets.py
    tests/unit/test_harness_markers.py
    tests/unit/test_host_bootstrap_strict_gate.py
    tests/unit/test_integration_check_script.py
  )
  if [[ "$run_python_static" == false ]]; then
    ruff check scripts/ "${gate_tooling_tests[@]}"
    ruff format --check scripts/ "${gate_tooling_tests[@]}"
  fi
  gate_pytest_jobs="$(nadili_gate_pytest_jobs)"
  gate_pytest_command=(
    python -m pytest "${gate_tooling_tests[@]}" -m "not integration" -q
    -n "$gate_pytest_jobs" --dist worksteal
  )
  nice_prefix_value="$(nadili_gate_nice_prefix)"
  if [[ -n "$nice_prefix_value" ]]; then
    nice_prefix=()
    read -r -a nice_prefix <<< "$nice_prefix_value"
    gate_pytest_command=("${nice_prefix[@]}" "${gate_pytest_command[@]}")
  fi
  "${gate_pytest_command[@]}"
fi

if [[ "$run_harness" == true ]]; then
  bash scripts/check-host-bootstrap.sh
fi

# Fail cheap dependency, lint, and type checks before starting the expensive Python suite.
if [[ "$run_web" == true ]]; then
  python scripts/check_design_tokens.py
  if [[ "$integration_context" == true && "$run_web_build" == true ]]; then
    bash scripts/web-check.sh full "$web_scope"
  else
    bash scripts/web-check.sh fast "$web_scope"
  fi
fi

if [[ "$run_python_unit" == true && "$run_harness" == false && "$run_gate_tooling" == false ]]; then
  python scripts/gate_evidence.py run --check python-unit
elif [[ "$run_python_unit" == true || "$run_harness" == true ]]; then
  # See NADILI_PYTEST_JOBS note in pre-commit.sh.
  pytest_jobs="$(nadili_gate_pytest_jobs)"
  nice_prefix_value="$(nadili_gate_nice_prefix)"
  if [[ "$run_python_unit" == true && "$run_harness" == true ]]; then
    pytest_marker="not integration"
  elif [[ "$run_harness" == true ]]; then
    pytest_marker="not integration and harness"
  else
    pytest_marker="not integration and not harness"
  fi
  pytest_command=(python -m pytest apps/api/tests tests/unit -m "$pytest_marker" -q -n "$pytest_jobs" --dist worksteal)
  if [[ "$run_gate_tooling" == true ]]; then
    for test_path in "${gate_tooling_tests[@]}"; do
      pytest_command+=(--ignore "$test_path")
    done
  fi
  if [[ -n "$nice_prefix_value" ]]; then
    nice_prefix=()
    read -r -a nice_prefix <<< "$nice_prefix_value"
    pytest_command=("${nice_prefix[@]}" "${pytest_command[@]}")
  fi
  "${pytest_command[@]}"
fi
if [[ "$explicit_capabilities" == false ]]; then
  python scripts/gate_evidence.py snapshot --tree "$verified_tree"
fi
echo "Fast checks passed"
