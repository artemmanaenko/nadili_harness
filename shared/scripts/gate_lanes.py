#!/usr/bin/env python3
"""Determine whether a commit-sanity gate lane must run."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import gate_test_selection

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Documentation paths the web tests actually read. `apps/web-relocation/src/lib/
#: brand-assets.test.ts` loads `docs/brand/nadili/*.svg`, so a brand-asset change breaks a public
#: web test even though it looks like documentation. Checked before ordinary docs routing.
WEB_ASSET_PREFIXES = ("docs/brand/",)
# This is deliberately a checked-in table rather than a filename heuristic.  A source change
# that cannot be proven to belong to one independent flow uses the full Admin suite below.
ADMIN_PATH_TO_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "apps/admin-web/src/components/sources/",
        ("apps/admin-web/e2e/source-web.spec.ts", "apps/admin-web/e2e/source-rss.spec.ts"),
    ),
    (
        "apps/admin-web/src/components/answers/",
        (
            "apps/admin-web/e2e/answer-review-wizard.spec.ts",
            "apps/admin-web/e2e/answer-catalog-plan-review.spec.ts",
        ),
    ),
    (
        "apps/admin-web/src/components/paths/",
        ("apps/admin-web/e2e/path-publication-workflow.spec.ts",),
    ),
    (
        "apps/admin-web/src/components/verification/",
        ("apps/admin-web/e2e/verification-requests.spec.ts",),
    ),
)
ADMIN_SHARED_PATH_PREFIXES = (
    "apps/admin-web/src/lib/",
    "apps/admin-web/e2e/admin-e2e-support.ts",
    "apps/admin-web/e2e/admin-e2e-origin.ts",
    "apps/admin-web/e2e/clerk.setup.ts",
    "apps/admin-web/playwright.config.ts",
)
CONTROL_PLANE_PREFIXES = ("scripts/", "infra/", ".github/")
INSTRUCTION_ONLY_PREFIXES = (".claude/", ".codex/", ".agents/")
# NAD-528: the only `.claude/` paths that are runtime inputs, not agent instructions.
CLAUDE_RUNTIME_PATHS = (
    ".claude/role-models.toml",
    ".claude/skills/codex-implement/scripts/",
)
CLAUDE_RUNTIME_TESTS = (
    "tests/unit/test_claude_transport.py",
    "tests/unit/test_claude_implement.py",
    "tests/unit/test_codex_role_dispatch.py",
    "tests/unit/test_codex_trip_skills.py",
)
INSTRUCTION_ONLY_NAMES = frozenset({"AGENTS.md", "CLAUDE.md", "skills-lock.json"})
PRODUCTION_SURFACE_PREFIXES = ("infra/bootstrap/", "infra/services/", ".github/workflows/")
PRODUCTION_SURFACE_GLOBS = (
    "scripts/prod-*.sh",
    "scripts/smoke-api.sh",
    "scripts/portability-smoke.sh",
)
GATE_TOOLING = (
    "scripts/changed_areas.py",
    "scripts/check-host-bootstrap.sh",
    "scripts/codex_*",
    "scripts/claude_*",
    "scripts/dev-check.sh",
    "scripts/gate_executor.py",
    "scripts/gate_lanes.py",
    "scripts/gate_evidence.py",
    "scripts/gate_test_selection.py",
    "scripts/integration-check.sh",
    "scripts/pre-commit*",
    "scripts/python-check.sh",
    "scripts/lib/**",
)
GATE_TOOLING_TESTS = frozenset(
    {
        "tests/unit/test_changed_areas.py",
        "tests/unit/test_check_item_ready.py",
        "tests/unit/test_check_plan_interleaving.py",
        "tests/unit/test_codex_code_review.py",
        "tests/unit/test_codex_orchestration_budget.py",
        "tests/unit/test_dev_check_script.py",
        "tests/unit/test_gate_executor.py",
        "tests/unit/test_gate_lanes.py",
        "tests/unit/test_gate_evidence.py",
        "tests/unit/test_gate_test_selection.py",
        "tests/unit/test_gate_slots_lib.py",
        "tests/unit/test_gate_test_targets.py",
        "tests/unit/test_harness_markers.py",
        "tests/unit/test_host_bootstrap_strict_gate.py",
        "tests/unit/test_integration_check_script.py",
    }
)
#: Root files that change the web dependency graph (spec R2): both apps, no Python lane.
WEB_DEPENDENCY_FILES = frozenset({"package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml"})
#: Root Python dependency pins (spec R2): every Python lane, no web lane.
PYTHON_DEPENDENCY_GLOBS = ("requirements*.in", "requirements*.txt")
PYTHON_DEPENDENCY_CAPABILITIES = (
    "client-python",
    "python-static",
    "python-unit",
    "backend-integration",
    "harness",
)
HEAVY_CAPABILITIES = frozenset({"full-baseline", "admin-e2e", "public-visual", "portability"})
BROAD_FALLBACK = (
    "client-python",
    "contracts",
    "python-static",
    "python-unit",
    "web-static",
    "web-unit",
    "web-build",
    "backend-integration",
    "harness",
)
CAPABILITY_ORDER = (
    "full-baseline",
    "client-python",
    "contracts",
    "gate-tooling",
    "python-static",
    "shell-syntax",
    "host-bootstrap-check",
    "python-unit",
    "selected-tests",
    "web-static",
    "web-unit",
    "web-build",
    "backend-integration",
    "selected-postgres-tests",
    "admin-e2e",
    "public-visual",
    "harness",
    "portability",
    "docs",
)
# Keep deterministic client, contract and combined web checks ahead of backend integration. This
# changes execution order only; the capability union and conservative fallback remain unchanged.
# Static consumers and selected unit cases run before any PostgreSQL lane (spec R5).


class RoutingError(ValueError):
    """Raised when a routing scope cannot be safely determined."""


@dataclass(frozen=True, slots=True)
class GateSelection:
    """The conservative capability union for one immutable path scope."""

    paths: tuple[str, ...]
    capabilities: tuple[str, ...]
    reasons: Mapping[str, str]
    admin_specs: tuple[str, ...] = ()
    web_scope: str = "all"
    #: Test files with cases no selected full lane executes, for the unit and PostgreSQL legs.
    selected_unit_tests: tuple[str, ...] = ()
    selected_postgres_tests: tuple[str, ...] = ()
    #: Changed shell scripts for `bash -n`.
    shell_paths: tuple[str, ...] = ()
    #: Selected test file -> a changed path that selected it (spec R7.2).
    test_reasons: Mapping[str, str] = field(default_factory=dict)
    #: Selected test file -> how that path reaches it: `imports`, `references`, `migrates` or
    #: `exercises` (NAD-469).
    test_verbs: Mapping[str, str] = field(default_factory=dict)
    #: Why a derived path kept today's selection (derivation unavailable).
    notes: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Return a JSON-safe routing result."""

        return {
            "paths": list(self.paths),
            "capabilities": list(self.capabilities),
            "reasons": dict(self.reasons),
            "admin_specs": list(self.admin_specs),
            "web_scope": self.web_scope,
            "selected_unit_tests": list(self.selected_unit_tests),
            "selected_postgres_tests": list(self.selected_postgres_tests),
            "shell_paths": list(self.shell_paths),
            "test_reasons": dict(self.test_reasons),
            "test_verbs": dict(self.test_verbs),
            "notes": list(self.notes),
        }


def _is_instruction_only_path(path: str) -> bool:
    """Return whether a path configures an agent without changing product runtime code."""
    if path.startswith(CLAUDE_RUNTIME_PATHS):
        return False
    return path.startswith(INSTRUCTION_ONLY_PREFIXES) or path.rsplit("/", 1)[-1] in (
        INSTRUCTION_ONLY_NAMES
    )


def _capability_union(capabilities: set[str]) -> tuple[str, ...]:
    return tuple(name for name in CAPABILITY_ORDER if name in capabilities)


def _is_admin_shared_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in ADMIN_SHARED_PATH_PREFIXES)


def _admin_specs_for_path(path: str) -> tuple[str, ...] | None:
    if _is_admin_shared_path(path):
        return None
    for prefix, specs in ADMIN_PATH_TO_SPECS:
        if path.startswith(prefix):
            return specs
    return None


def _add_capabilities(
    capabilities: set[str], reasons: dict[str, str], names: Sequence[str], reason: str
) -> None:
    for name in names:
        capabilities.add(name)
        reasons.setdefault(name, reason)


def _matches_glob(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def _merge_web_scope(current: str | None, incoming: str) -> str:
    """Combine app-specific web consumers, retaining both when a shared input is present."""
    if current is None:
        return incoming
    if current == incoming:
        return current
    return "all"


def is_derived_path(path: str) -> bool:
    """Whether light-mode routing selects a path's tests from the derived exercise map.

    Control-plane paths (spec R1), test helpers and container-build files (R2), test files (R3)
    and, since NAD-469, backend application code: a module selects the tests that import or name
    it, and its R2 paths add the complete selection once the map has classified them
    (`_route_application_path`). Every `conftest.py`, the pinned gate-tooling tests and the
    operator package keep their explicit rules.
    """

    name = path.rsplit("/", 1)[-1]
    if name == "conftest.py" or path in GATE_TOOLING_TESTS:
        return False
    if path.startswith("packages/operator-python/") or path == "scripts/operator.sh":
        return False
    return (
        gate_test_selection.is_test_file(path)
        or gate_test_selection.is_helper_module(path)
        or gate_test_selection.is_selectable(path)
    )


#: Backend dependency pins: every Python lane, as NAD-459 R2 routes the root pins.
APPLICATION_DEPENDENCY_GLOBS = ("apps/api/requirements*",)
#: The complete backend selection an R2 path keeps (spec NAD-469 R2).
COMPLETE_BACKEND_CAPABILITIES = ("python-static", "python-unit", "backend-integration")


def _route_application_path(
    path: str,
    capabilities: set[str],
    reasons: dict[str, str],
    *,
    exercise_map: gate_test_selection.ExerciseMap,
    deleted: frozenset[str],
) -> None:
    """Add the complete selection a derived backend application path keeps (spec NAD-469 R2).

    Its derived tests are selected in any case, which also runs harness cases that name an R2
    path. Checked in order: dependency pins (every Python lane), documentation (the docs rule
    only), container-build files (NAD-459 R2, derived only), the R2 paths, then any other
    non-Python file that no test names.
    """

    try:
        complete = gate_test_selection.requires_complete_backend_selection(
            path, exercise_map, deleted
        )
    except gate_test_selection.SelectionIncomplete as exc:
        raise RoutingError(f"test selection is incomplete: {exc}") from exc
    if _matches_glob(path, APPLICATION_DEPENDENCY_GLOBS):
        _add_capabilities(
            capabilities,
            reasons,
            PYTHON_DEPENDENCY_CAPABILITIES,
            "Python dependency pins affect every Python lane",
        )
    elif path.endswith(".md") or gate_test_selection.is_container_target(path):
        return
    elif complete:
        _add_capabilities(
            capabilities,
            reasons,
            COMPLETE_BACKEND_CAPABILITIES,
            f"{path} is schema, settings, package or conftest code that every backend test runs",
        )
    elif not path.endswith(".py") and not gate_test_selection.is_referenced(path, exercise_map):
        _add_capabilities(
            capabilities,
            reasons,
            COMPLETE_BACKEND_CAPABILITIES,
            f"no test names backend file {path}; keep the complete backend selection",
        )


def _execution_plan(
    selection: gate_test_selection.TestSelection,
    capabilities: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Selected files whose trees no selected full lane covers (spec R7.1), per channel.

    Subtraction here is by tree and lane only; the runners subtract by marker through collection
    (`gate_test_selection.plan_groups`), so a file listed here may still plan no run.
    """

    unit: list[str] = []
    for path in selection.unit_tests:
        marker = gate_test_selection.unit_marker(
            path,
            python_unit="python-unit" in capabilities,
            harness="harness" in capabilities,
            gate_tooling="gate-tooling" in capabilities,
            gate_tooling_tests=GATE_TOOLING_TESTS,
        )
        if marker is not None:
            unit.append(path)
    postgres = [
        path
        for path in selection.postgres_tests
        if gate_test_selection.postgres_needed(
            path, backend_integration="backend-integration" in capabilities
        )
    ]
    return tuple(unit), tuple(postgres)


def select_capabilities(
    paths: Sequence[str],
    *,
    heavy: bool = False,
    version_only_pyproject: bool = False,
    exercise_map: gate_test_selection.ExerciseMap | None = None,
    deleted: frozenset[str] = frozenset(),
    unavailable_reason: str | None = None,
) -> GateSelection:
    """Select the minimum conservative capability union for changed paths.

    Empty input and unknown paths intentionally select the broad fallback.  A caller may never
    turn an incomplete diff into a successful no-op by accident.

    Light mode routes derived paths (`is_derived_path`) through `exercise_map`. Without one --
    not requested, or `unavailable_reason` says why it could not be built -- those paths keep
    today's selection; unavailability is never an empty selection.
    """

    normalized = tuple(dict.fromkeys(path for path in paths if path))
    capabilities: set[str] = set()
    reasons: dict[str, str] = {}
    admin_specs: set[str] = set()
    full_reason: str | None = None
    web_scope: str | None = None
    admin_requires_full_suite = False
    fallback = ("full-baseline",) if heavy else BROAD_FALLBACK
    derived: list[str] = []
    notes: list[str] = []

    if not normalized:
        full_reason = "empty change scope; run the full affected baseline"

    for path in normalized:
        if path.startswith(CLAUDE_RUNTIME_PATHS):
            # NAD-528: runtime inputs under .claude/ select the tests that exercise them; light
            # mode derives those test files, otherwise the whole unit lane runs.
            reason = "Claude runtime configuration requires its behavioural tests"
            _add_capabilities(capabilities, reasons, ("gate-tooling", "python-static"), reason)
            if not heavy and exercise_map is not None:
                derived.extend(CLAUDE_RUNTIME_TESTS)
            else:
                _add_capabilities(capabilities, reasons, ("python-unit",), reason)
            continue
        if _is_instruction_only_path(path):
            _add_capabilities(
                capabilities,
                reasons,
                ("docs",),
                "agent instructions require validation but no product test lane",
            )
            continue
        if not heavy:
            if path == "pyproject.toml" and version_only_pyproject:
                _add_capabilities(
                    capabilities,
                    reasons,
                    ("docs",),
                    "release version bump: only [project].version changed",
                )
                continue
            if path in WEB_DEPENDENCY_FILES:
                _add_capabilities(
                    capabilities,
                    reasons,
                    ("web-static", "web-unit", "web-build"),
                    "web dependency graph change affects both web applications",
                )
                web_scope = _merge_web_scope(web_scope, "all")
                continue
            if "/" not in path and _matches_glob(path, PYTHON_DEPENDENCY_GLOBS):
                _add_capabilities(
                    capabilities,
                    reasons,
                    PYTHON_DEPENDENCY_CAPABILITIES,
                    "Python dependency pins affect every Python lane",
                )
                continue
            if is_derived_path(path):
                if exercise_map is not None:
                    derived.append(path)
                    if _matches_glob(path, GATE_TOOLING):
                        _add_capabilities(
                            capabilities,
                            reasons,
                            ("gate-tooling", "python-static"),
                            "gate tooling changes require focused tests and strict Python checks",
                        )
                    if path.endswith(".md"):
                        _add_capabilities(capabilities, reasons, ("docs",), "documentation change")
                    if gate_test_selection.is_application_path(path):
                        _route_application_path(
                            path,
                            capabilities,
                            reasons,
                            exercise_map=exercise_map,
                            deleted=deleted,
                        )
                    continue
                if unavailable_reason is not None:
                    notes.append(f"{path}: derivation unavailable ({unavailable_reason})")
            if _matches_glob(path, APPLICATION_DEPENDENCY_GLOBS):
                # Without a derivation the backend pins still select every Python lane (R2).
                _add_capabilities(
                    capabilities,
                    reasons,
                    PYTHON_DEPENDENCY_CAPABILITIES,
                    "Python dependency pins affect every Python lane",
                )
                continue
        if heavy:
            if path.startswith(CONTROL_PLANE_PREFIXES) or path.startswith(
                "apps/web-relocation/tests/"
            ):
                full_reason = (
                    full_reason or f"{path} is shared control-plane or test infrastructure"
                )
                continue
        else:
            if path.startswith(PRODUCTION_SURFACE_PREFIXES) or _matches_glob(
                path, PRODUCTION_SURFACE_GLOBS
            ):
                _add_capabilities(
                    capabilities,
                    reasons,
                    BROAD_FALLBACK,
                    "production surface changes are covered by candidate qualification",
                )
                web_scope = "all"
                continue
            if _matches_glob(path, GATE_TOOLING):
                _add_capabilities(
                    capabilities,
                    reasons,
                    ("gate-tooling", "python-static"),
                    "gate tooling changes require focused tests and strict Python checks",
                )
                continue
        if path.startswith(WEB_ASSET_PREFIXES):
            _add_capabilities(
                capabilities,
                reasons,
                ("web-static", "web-unit", "web-build", "public-visual"),
                "public web tests consume this checked-in asset",
            )
            web_scope = _merge_web_scope(web_scope, "public")
            continue
        if path.startswith("docs/") or path.endswith(".md"):
            _add_capabilities(capabilities, reasons, ("docs",), "ordinary documentation change")
            continue
        if path == "nadili_runtime/pyproject.toml":
            _add_capabilities(
                capabilities,
                reasons,
                ("python-static", "python-unit", "backend-integration", "client-python"),
                "canonical runtime packaging changes require installed operator verification",
            )
            continue
        if path.startswith(("apps/api/", "nadili_runtime/")):
            _add_capabilities(
                capabilities,
                reasons,
                ("python-static", "python-unit", "backend-integration"),
                "backend runtime or persistence code can affect integration behavior",
            )
            continue
        if path.startswith("core/"):
            _add_capabilities(
                capabilities,
                reasons,
                ("python-static", "python-unit"),
                "shared Python runtime code affects backend consumers",
            )
            continue
        if path.startswith("packages/client-python/"):
            _add_capabilities(
                capabilities,
                reasons,
                ("python-static", "python-unit", "client-python", "contracts"),
                "standalone Python client and its backend contract are coupled",
            )
            continue
        if path.startswith("packages/operator-python/") or path == "scripts/operator.sh":
            _add_capabilities(
                capabilities,
                reasons,
                ("python-static", "python-unit", "client-python"),
                "standalone operator CLI and its installed package need focused Python coverage",
            )
            continue
        if path.startswith(("packages/client-ts/", "packages/contracts/")):
            _add_capabilities(
                capabilities,
                reasons,
                ("contracts", "web-static", "web-unit", "web-build"),
                "generated/client contracts propagate to both web consumers",
            )
            web_scope = _merge_web_scope(web_scope, "all")
            continue
        if path.startswith("packages/ui/"):
            _add_capabilities(
                capabilities,
                reasons,
                ("web-static", "web-unit", "web-build"),
                "shared UI is consumed by both web applications",
            )
            web_scope = _merge_web_scope(web_scope, "all")
            continue
        if path.startswith("apps/admin-web/"):
            _add_capabilities(
                capabilities,
                reasons,
                ("web-static", "web-unit", "web-build", "admin-e2e"),
                "Admin application change requires production behavior coverage",
            )
            specs = _admin_specs_for_path(path)
            if specs is None:
                # An unmapped Admin path needs the complete Admin journey suite, not unrelated
                # Python clients, public visual checks, or the full repository baseline.
                reasons.setdefault(
                    "admin-e2e", f"{path} has shared Admin fixtures or no proven flow map"
                )
                admin_requires_full_suite = True
            else:
                admin_specs.update(specs)
            web_scope = _merge_web_scope(web_scope, "admin")
            continue
        if path.startswith("apps/web-relocation/"):
            _add_capabilities(
                capabilities,
                reasons,
                ("web-static", "web-unit", "web-build", "public-visual"),
                "public web runtime change requires visual smoke coverage",
            )
            web_scope = _merge_web_scope(web_scope, "public")
            continue
        if path.startswith("tests/integration/"):
            _add_capabilities(
                capabilities,
                reasons,
                ("python-static", "python-unit", "backend-integration"),
                "integration test changes require strict typing and PostgreSQL-backed tests",
            )
            continue
        if path in GATE_TOOLING_TESTS:
            _add_capabilities(
                capabilities,
                reasons,
                ("gate-tooling", "python-static"),
                "gate-tooling test changes require focused tests and strict Python checks",
            )
            continue
        if path.startswith("tests/unit/"):
            _add_capabilities(
                capabilities,
                reasons,
                ("python-static", "python-unit"),
                "unit test changes require strict typing and the affected test lane",
            )
            continue
        full_reason = full_reason or f"unknown path {path}; use the broad fallback"

    if admin_requires_full_suite:
        admin_specs.clear()

    if full_reason is not None:
        if heavy:
            capabilities = set(fallback)
            reasons = {"full-baseline": full_reason}
            admin_specs.clear()
            web_scope = "all"
        else:
            _add_capabilities(capabilities, reasons, fallback, full_reason)
            web_scope = "all"

    if not heavy:
        for name in tuple(capabilities & HEAVY_CAPABILITIES):
            original_reason = reasons[name]
            reasons[name] = f"deferred to candidate qualification: {name} ({original_reason})"
            capabilities.remove(name)
        # The spec list exists only to narrow `admin-e2e`. With that capability deferred nothing
        # consumes it, and emitting it on the --admin-specs-only channel would invite a caller to
        # run the very suite this mode exists to defer.
        admin_specs.clear()

    unit_tests: tuple[str, ...] = ()
    postgres_tests: tuple[str, ...] = ()
    shell_paths: tuple[str, ...] = ()
    test_reasons: dict[str, str] = {}
    test_verbs: dict[str, str] = {}
    if derived and exercise_map is not None:
        try:
            selection = gate_test_selection.select(
                derived, deleted=deleted, exercise_map=exercise_map
            )
        except gate_test_selection.SelectionIncomplete as exc:
            raise RoutingError(f"test selection is incomplete: {exc}") from exc
        for consumer in selection.static_consumers:
            _add_capabilities(
                capabilities,
                reasons,
                (consumer,),
                f"{selection.reasons[consumer]} is checked by this static consumer",
            )
        # Deduplicate against the final full-lane union, fallback included (spec R7.1).
        unit_tests, postgres_tests = _execution_plan(selection, capabilities)
        test_reasons = {
            test: selection.reasons[test] for test in sorted({*unit_tests, *postgres_tests})
        }
        test_verbs = {test: selection.verbs.get(test, "exercises") for test in test_reasons}
        if unit_tests:
            _add_capabilities(
                capabilities,
                reasons,
                ("selected-tests",),
                f"{len(unit_tests)} test files exercise the changed derived paths",
            )
        if postgres_tests:
            _add_capabilities(
                capabilities,
                reasons,
                ("selected-postgres-tests",),
                f"{len(postgres_tests)} PostgreSQL test files exercise the changed derived paths",
            )
        if not (selection.unit_tests or selection.postgres_tests or selection.static_consumers):
            _add_capabilities(
                capabilities,
                reasons,
                ("docs",),
                "no test or static consumer exercises the changed derived paths",
            )
        shell_paths = selection.shell_paths if "shell-syntax" in capabilities else ()

    selected_capabilities = _capability_union(capabilities)
    if not selected_capabilities:
        raise RoutingError("routing selected zero capabilities")
    return GateSelection(
        paths=normalized,
        capabilities=selected_capabilities,
        reasons=reasons,
        admin_specs=tuple(sorted(admin_specs)),
        web_scope=web_scope or "all",
        selected_unit_tests=unit_tests,
        selected_postgres_tests=postgres_tests,
        shell_paths=shell_paths,
        test_reasons=test_reasons,
        test_verbs=test_verbs,
        notes=tuple(notes),
    )


def require_test_selection(test_paths: Sequence[str], label: str) -> tuple[str, ...]:
    """Reject a capability request that resolved to no executable tests."""

    selected = tuple(dict.fromkeys(path for path in test_paths if path))
    if not selected:
        raise RoutingError(f"{label} selected zero tests")
    return selected


def _git_output(arguments: Sequence[str]) -> list[str]:
    git = shutil.which("git")
    if git is None:
        raise RoutingError("git is required to inspect the change scope")
    result = subprocess.run(  # noqa: S603 -- fixed git argv, no shell.
        [git, *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _diff_paths(arguments: Sequence[str]) -> list[str]:
    # --no-renames makes a rename an explicit delete plus add, retaining both routing sides.
    return _git_output(["diff", *arguments, "--name-only", "--diff-filter=ACMRD", "--no-renames"])


def changed_paths(
    *,
    staged: bool = False,
    dirty: bool = False,
    base: str | None = None,
    head: str = "HEAD",
) -> list[str]:
    """Return the complete path union for one routing scope."""

    selected_modes = sum((staged, dirty, base is not None))
    if selected_modes != 1:
        raise RoutingError("choose exactly one of staged, dirty, or base/head")
    if staged:
        return _diff_paths(["--cached"])
    if dirty:
        return list(
            dict.fromkeys(
                _diff_paths([])
                + _diff_paths(["--cached"])
                + _git_output(["ls-files", "--others", "--exclude-standard"])
            )
        )
    if base is None or not base.strip():
        raise RoutingError("integration base is required")
    return _diff_paths([base, head])


def _scope_arguments(*, staged: bool, base: str | None, head: str) -> list[str]:
    if staged:
        return ["--cached"]
    if base is None or not base.strip():
        raise RoutingError("integration base is required")
    return [base, head]


def deleted_paths(
    *, staged: bool = False, base: str | None = None, head: str = "HEAD"
) -> frozenset[str]:
    """Paths the light-mode scope deletes; with `--no-renames` a rename source is one."""

    arguments = _scope_arguments(staged=staged, base=base, head=head)
    return frozenset(
        _git_output(["diff", *arguments, "--name-only", "--diff-filter=D", "--no-renames"])
    )


_VERSION_ASSIGNMENT = re.compile(r"\s*version\s*=")
_TABLE_HEADER = re.compile(r"\s*\[")


def _table_of(lines: Sequence[str], index: int) -> str | None:
    for line in reversed(lines[:index]):
        if _TABLE_HEADER.match(line):
            return line.strip()
    return None


def version_only_change(before: str, after: str) -> bool:
    """Whether two `pyproject.toml` texts differ only in the top-level `[project].version` value.

    Any other hunk, a nested key named `version`, an unchanged version or malformed TOML is not a
    version-only change (spec R4).
    """

    try:
        old = tomllib.loads(before)
        new = tomllib.loads(after)
    except tomllib.TOMLDecodeError:
        return False
    old_project, new_project = old.get("project"), new.get("project")
    if not (isinstance(old_project, dict) and isinstance(new_project, dict)):
        return False
    if "version" not in old_project or "version" not in new_project:
        return False
    if old_project["version"] == new_project["version"]:
        return False
    old_rest = {**old, "project": {k: v for k, v in old_project.items() if k != "version"}}
    new_rest = {**new, "project": {k: v for k, v in new_project.items() if k != "version"}}
    if old_rest != new_rest:
        return False
    old_lines, new_lines = before.splitlines(), after.splitlines()
    if len(old_lines) != len(new_lines):
        return False
    changed = [index for index, line in enumerate(old_lines) if line != new_lines[index]]
    if len(changed) != 1:
        return False
    index = changed[0]
    return all(
        _VERSION_ASSIGNMENT.match(lines[index]) is not None
        and _table_of(lines, index) == "[project]"
        for lines in (old_lines, new_lines)
    )


def _git_text(revision_path: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RoutingError("git is required to inspect the change scope")
    result = subprocess.run(  # noqa: S603 -- fixed git argv, no shell.
        [git, "show", revision_path],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def pyproject_version_only(
    *, staged: bool = False, base: str | None = None, head: str = "HEAD"
) -> bool:
    """Whether the scope's `pyproject.toml` change is a release version bump; failure is not."""

    if staged:
        before_ref, after_ref = "HEAD:pyproject.toml", ":pyproject.toml"
    elif base is not None:
        before_ref, after_ref = f"{base}:pyproject.toml", f"{head}:pyproject.toml"
    else:
        return False
    try:
        return version_only_change(_git_text(before_ref), _git_text(after_ref))
    except (RoutingError, subprocess.CalledProcessError):
        return False


def build_light_selection(
    paths: Sequence[str], *, staged: bool, base: str | None, head: str
) -> GateSelection:
    """One light-mode routing computation: scope facts, the derivation and the selection."""

    deleted = deleted_paths(staged=staged, base=base, head=head)
    version_only = "pyproject.toml" in paths and pyproject_version_only(
        staged=staged, base=base, head=head
    )
    exercise_map: gate_test_selection.ExerciseMap | None = None
    unavailable: str | None = None
    if any(is_derived_path(path) for path in paths):
        try:
            exercise_map = gate_test_selection.build_exercise_map(PROJECT_ROOT)
        except gate_test_selection.SelectionUnavailable as exc:
            unavailable = str(exc)
        except gate_test_selection.SelectionIncomplete as exc:
            raise RoutingError(f"test selection is incomplete: {exc}") from exc
    return select_capabilities(
        paths,
        version_only_pyproject=version_only,
        exercise_map=exercise_map,
        deleted=deleted,
        unavailable_reason=unavailable,
    )


def _print_selection(
    selection: GateSelection,
    *,
    json_output: bool,
    capabilities_only: bool,
    admin_specs_only: bool,
    paths_only: bool,
    web_scope_only: bool,
    shell_output: bool = False,
) -> None:
    if paths_only:
        for path in selection.paths:
            print(path)
        return
    if web_scope_only:
        print(selection.web_scope)
        return
    if admin_specs_only:
        for spec in selection.admin_specs:
            print(spec)
        return
    if json_output:
        print(json.dumps(selection.to_mapping(), sort_keys=True))
        return
    if shell_output:
        for record in shell_records(selection):
            print(record)
        return
    if capabilities_only:
        for name in selection.capabilities:
            print(name)
        return
    for line in summary_lines(selection):
        print(line)


def summary_lines(selection: GateSelection) -> list[str]:
    """The human-readable routing summary every gate prints."""

    lines: list[str] = []
    for name in selection.capabilities:
        lines.append(f"capability: {name}")
        lines.append(f"reason: {selection.reasons[name]}")
    for name in CAPABILITY_ORDER:
        if name not in selection.capabilities and name in selection.reasons:
            reason = selection.reasons[name]
            if reason.startswith("deferred to candidate qualification:"):
                lines.append(f"reason: {reason}")
    if selection.admin_specs:
        lines.append("admin-specs: " + " ".join(selection.admin_specs))
    for test, path in selection.test_reasons.items():
        verb = selection.test_verbs.get(test, "exercises")
        lines.append(f"selected-test: {test} ({verb} {path})")
    for note in selection.notes:
        lines.append(f"note: {note}")
    return lines


def shell_records(selection: GateSelection) -> list[str]:
    """The single routing result as `kind<TAB>value` lines for the shell runners."""

    fields: list[tuple[str, str]] = [("summary", line) for line in summary_lines(selection)]
    fields += [("capability", name) for name in selection.capabilities]
    fields += [("path", path) for path in selection.paths]
    fields.append(("web-scope", selection.web_scope))
    fields += [("admin-spec", spec) for spec in selection.admin_specs]
    fields += [("unit-test", path) for path in selection.selected_unit_tests]
    fields += [("postgres-test", path) for path in selection.selected_postgres_tests]
    fields += [("shell", path) for path in selection.shell_paths]
    records: list[str] = []
    for kind, value in fields:
        if any(character in value for character in "\t\n\r"):
            raise RoutingError(f"unsupported control character in routed value {value!r}")
        records.append(f"{kind}\t{value}")
    return records


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--dirty", action="store_true")
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--capabilities-only", action="store_true")
    parser.add_argument("--admin-specs-only", action="store_true")
    parser.add_argument("--paths-only", action="store_true")
    parser.add_argument("--web-scope-only", action="store_true")
    parser.add_argument(
        "--shell",
        action="store_true",
        help="emit the single routing result as kind<TAB>value records for the gate runners",
    )
    args = parser.parse_args(argv)
    if args.integration:
        if sum((args.staged, args.dirty, args.base is not None)) != 1:
            parser.error("integration scope flags are mutually exclusive")
        if args.commit and not args.staged:
            parser.error("--commit requires --staged")
        if (
            sum(
                (
                    args.capabilities_only,
                    args.admin_specs_only,
                    args.paths_only,
                    args.web_scope_only,
                    args.shell,
                    args.json,
                )
            )
            > 1
        ):
            parser.error("choose one selection output channel")
        heavy = (args.dirty or args.staged) and not args.commit
        try:
            paths = changed_paths(
                staged=args.staged,
                dirty=args.dirty,
                base=args.base,
                head=args.head,
            )
            if heavy:
                selection = select_capabilities(paths, heavy=True)
            else:
                selection = build_light_selection(
                    paths, staged=args.staged, base=args.base, head=args.head
                )
            _print_selection(
                selection,
                json_output=args.json,
                capabilities_only=args.capabilities_only,
                admin_specs_only=args.admin_specs_only,
                paths_only=args.paths_only,
                web_scope_only=args.web_scope_only,
                shell_output=args.shell,
            )
        except (RoutingError, subprocess.CalledProcessError) as exc:
            print(f"gate routing failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return 0
    parser.error("--integration is required")


if __name__ == "__main__":
    raise SystemExit(main())
