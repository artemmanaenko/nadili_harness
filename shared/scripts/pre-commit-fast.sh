#!/bin/bash
# Fast commit sanity gate for backend and web feedback.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

usage() {
  printf 'Usage: bash scripts/pre-commit-fast.sh [--integration] [--web-scope all|admin|public] [--capability NAME]... [--selected-test PATH]... [--shell-file PATH]...\n' >&2
}

explicit_capabilities=false
integration_context=false
web_scope=all
requested_capabilities=()
selected_tests=()
shell_files=()
while (($# > 0)); do
  case "$1" in
    --integration)
      integration_context=true
      shift
      ;;
    --web-scope)
      (($# >= 2)) || { usage; exit 2; }
      case "$2" in
        all|admin|public) web_scope="$2" ;;
        *) printf 'pre-commit-fast: invalid web scope %s\n' "$2" >&2; exit 2 ;;
      esac
      shift 2
      ;;
    --capability)
      (($# >= 2)) || { usage; exit 2; }
      case "$2" in
        gate-tooling|python-static|python-unit|web-static|web-unit|web-build|harness|selected-tests|host-bootstrap-check|shell-syntax)
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
    --selected-test)
      (($# >= 2)) || { usage; exit 2; }
      selected_tests+=("$2")
      shift 2
      ;;
    --shell-file)
      (($# >= 2)) || { usage; exit 2; }
      shell_files+=("$2")
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

# Explicit lists and their capabilities must agree; paths stay argv data and are validated as
# tracked files before anything runs.
requested_list=" ${requested_capabilities[*]-} "
if { [[ "$requested_list" == *" selected-tests "* ]] && ((${#selected_tests[@]} == 0)); } ||
   { [[ "$requested_list" != *" selected-tests "* ]] && ((${#selected_tests[@]} > 0)); }; then
  printf 'pre-commit-fast: --capability selected-tests and --selected-test must be given together\n' >&2
  exit 2
fi
if { [[ "$requested_list" == *" shell-syntax "* ]] && ((${#shell_files[@]} == 0)); } ||
   { [[ "$requested_list" != *" shell-syntax "* ]] && ((${#shell_files[@]} > 0)); }; then
  printf 'pre-commit-fast: --capability shell-syntax and --shell-file must be given together\n' >&2
  exit 2
fi

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
  # One routing computation per gate run: every leg below consumes this single result.
  routing_records="$(python scripts/gate_lanes.py --integration --staged --commit --shell)"
  staged_path_count=0
  while IFS=$'\t' read -r record_kind record_value; do
    case "$record_kind" in
      '') ;;
      summary) printf '%s\n' "$record_value" ;;
      capability) requested_capabilities+=("$record_value") ;;
      path) staged_path_count=$((staged_path_count + 1)) ;;
      web-scope) web_scope="$record_value" ;;
      unit-test) selected_tests+=("$record_value") ;;
      shell) shell_files+=("$record_value") ;;
      admin-spec|postgres-test) ;;
      *) printf 'pre-commit-fast: unknown routing record %s\n' "$record_kind" >&2; exit 1 ;;
    esac
  done <<< "$routing_records"
  if ((staged_path_count == 0)); then
    printf 'pre-commit-fast: no staged changes; stage the intended paths or use scripts/dev-check.sh for dirty-scope checks\n' >&2
    exit 2
  fi
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
run_selected_tests=false
run_host_bootstrap_check=false
run_shell_syntax=false
for capability in ${requested_capabilities[@]+"${requested_capabilities[@]}"}; do
  case "$capability" in
    gate-tooling) run_gate_tooling=true ;;
    python-static) run_python_static=true ;;
    python-unit) run_python_unit=true ;;
    harness) run_harness=true ;;
    web-static|web-unit) run_web=true ;;
    web-build) run_web=true; run_web_build=true ;;
    selected-tests) run_selected_tests=true ;;
    host-bootstrap-check) run_host_bootstrap_check=true ;;
    shell-syntax) run_shell_syntax=true ;;
    docs) ;;
  esac
done

if [[ "$run_selected_tests" == true && ${#selected_tests[@]} -eq 0 ]]; then
  printf 'pre-commit-fast: selected-tests requires at least one --selected-test\n' >&2
  exit 2
fi
if [[ "$run_shell_syntax" == true && ${#shell_files[@]} -eq 0 ]]; then
  printf 'pre-commit-fast: shell-syntax requires at least one --shell-file\n' >&2
  exit 2
fi

if [[ "$run_gate_tooling" == false && "$run_python_static" == false && \
      "$run_python_unit" == false && "$run_selected_tests" == false && \
      "$run_host_bootstrap_check" == false && "$run_shell_syntax" == false && \
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
  tests/unit/test_gate_test_selection.py
  tests/unit/test_gate_slots_lib.py
  tests/unit/test_gate_test_targets.py
  tests/unit/test_harness_markers.py
  tests/unit/test_host_bootstrap_strict_gate.py
  tests/unit/test_integration_check_script.py
)

if [[ "$run_python_static" == true ]]; then
  python scripts/gate_evidence.py run --check python-static
fi

if [[ "$run_shell_syntax" == true ]]; then
  for shell_file in "${shell_files[@]}"; do
    if ! git ls-files --error-unmatch -- "$shell_file" >/dev/null 2>&1 || [[ ! -f "$shell_file" ]]; then
      printf 'pre-commit-fast: not a tracked shell script: %s\n' "$shell_file" >&2
      exit 2
    fi
    bash -n -- "$shell_file"
  done
  printf 'Shell syntax checks passed for %s file(s)\n' "${#shell_files[@]}"
fi

if [[ "$run_gate_tooling" == true ]]; then
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

if [[ "$run_harness" == true || "$run_host_bootstrap_check" == true ]]; then
  bash scripts/check-host-bootstrap.sh
fi

# Selected cases run once each: the planner subtracts what the selected full lanes execute, by
# tree and then by marker, and drops partitions proven empty. These runs are never evidence.
if [[ "$run_selected_tests" == true ]]; then
  plan_arguments=(plan-unit)
  [[ "$run_python_unit" == true ]] && plan_arguments+=(--python-unit)
  [[ "$run_harness" == true ]] && plan_arguments+=(--harness)
  if [[ "$run_gate_tooling" == true ]]; then
    plan_arguments+=(--gate-tooling)
    for test_path in "${gate_tooling_tests[@]}"; do
      plan_arguments+=(--gate-tooling-test "$test_path")
    done
  fi
  selected_plan="$(python scripts/gate_test_selection.py "${plan_arguments[@]}" -- "${selected_tests[@]}")"
  selected_jobs="$(nadili_gate_pytest_jobs)"
  nice_prefix_value="$(nadili_gate_nice_prefix)"
  while IFS=$'\t' read -r -a selected_group; do
    ((${#selected_group[@]} >= 2)) || continue
    selected_marker="${selected_group[0]}"
    selected_command=(
      python -m pytest -m "$selected_marker" -q -n "$selected_jobs" --dist worksteal --
      "${selected_group[@]:1}"
    )
    if [[ -n "$nice_prefix_value" ]]; then
      nice_prefix=()
      read -r -a nice_prefix <<< "$nice_prefix_value"
      selected_command=("${nice_prefix[@]}" "${selected_command[@]}")
    fi
    printf 'Selected tests (%s): %s file(s)\n' "$selected_marker" "$((${#selected_group[@]} - 1))"
    "${selected_command[@]}"
  done <<< "$selected_plan"
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
