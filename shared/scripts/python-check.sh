#!/usr/bin/env bash
set -euo pipefail
umask 077

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
source "$root/venv/bin/activate"
source "$root/scripts/lib/gate-slots.sh"
source "$root/scripts/lib/mypy-cache.sh"
nadili_gate_run_register
cleanup() {
  nadili_gate_mypy_cache_release
  nadili_gate_run_unregister
}
trap cleanup EXIT

if [[ "${1:-}" == --inputs ]]; then
  python -c 'import json, shutil, sys, sysconfig
names = ("python", "ruff", "mypy", "pytest", "bash", "git", "nice", "awk", "ps", "tr", "sleep", "mkdir", "rm", "cat", "kill", "sysctl", "nproc", "mv", "rmdir")
print(json.dumps([sys.executable, sysconfig.get_paths(), sys.path, {name: shutil.which(name) for name in names}]))'
  exit 0
fi

if [[ "${1:-}" == --identity ]]; then
  python -c 'from scripts.gate_evidence import _environment_identity; print(_environment_identity())'
  case "${2:-}" in
    python-static) printf 'fixed-python-static-v1\n' ;;
    python-unit)
      printf 'workers=%s\nnice=%s\ncurrent_nice=%s\n' \
        "$(nadili_gate_pytest_jobs)" "$(nadili_gate_nice_prefix)" "$(nadili_gate_current_nice)"
      ;;
    *) printf 'python-check: invalid identity target\n' >&2; exit 2 ;;
  esac
  exit 0
fi

case "${1:-}" in
  python-static)
    ruff check apps/api/ apps/pdf_parser/ core/pdf_documents.py nadili_runtime/ scripts/ tests/integration/ tests/unit/
    ruff format --check apps/api/ apps/pdf_parser/ core/pdf_documents.py nadili_runtime/ scripts/ tests/integration/ tests/unit/
    mypy_cache_dir="$(nadili_gate_mypy_cache_dir "$root" "$root/venv/bin/python")"
    nadili_gate_mypy_cache_acquire "$mypy_cache_dir"
    mypy apps/api/ apps/pdf_parser/ core/pdf_documents.py --strict --ignore-missing-imports --exclude '/tests/' \
      --cache-dir "$nadili_mypy_cache_dir"
    nadili_gate_mypy_cache_release
    ;;
  python-unit)
    pytest_jobs="$(nadili_gate_pytest_jobs)"
    nice_prefix_value="$(nadili_gate_nice_prefix)"
    pytest_command=(python -m pytest apps/api/tests tests/unit \
      -m "not integration and not harness" -q -n "$pytest_jobs" --dist worksteal)
    if [[ -n "$nice_prefix_value" ]]; then
      nice_prefix=()
      read -r -a nice_prefix <<< "$nice_prefix_value"
      pytest_command=("${nice_prefix[@]}" "${pytest_command[@]}")
    fi
    "${pytest_command[@]}"
    ;;
  *)
    printf 'python-check: invalid fixed check\n' >&2
    exit 2
    ;;
esac
