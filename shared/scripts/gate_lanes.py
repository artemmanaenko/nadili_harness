#!/usr/bin/env python3
"""Determine whether a commit-sanity gate lane must run."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

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
    "scripts/dev-check.sh",
    "scripts/gate_executor.py",
    "scripts/gate_lanes.py",
    "scripts/gate_evidence.py",
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
        "tests/unit/test_gate_slots_lib.py",
        "tests/unit/test_gate_test_targets.py",
        "tests/unit/test_harness_markers.py",
        "tests/unit/test_host_bootstrap_strict_gate.py",
        "tests/unit/test_integration_check_script.py",
    }
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
    "python-unit",
    "web-static",
    "web-unit",
    "web-build",
    "backend-integration",
    "admin-e2e",
    "public-visual",
    "harness",
    "portability",
    "docs",
)
# Keep deterministic client, contract and combined web checks ahead of backend integration. This
# changes execution order only; the capability union and conservative fallback remain unchanged.


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

    def to_mapping(self) -> dict[str, object]:
        """Return a JSON-safe routing result."""

        return {
            "paths": list(self.paths),
            "capabilities": list(self.capabilities),
            "reasons": dict(self.reasons),
            "admin_specs": list(self.admin_specs),
            "web_scope": self.web_scope,
        }


def _is_instruction_only_path(path: str) -> bool:
    """Return whether a path configures an agent without changing product runtime code."""
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


def select_capabilities(paths: Sequence[str], *, heavy: bool = False) -> GateSelection:
    """Select the minimum conservative capability union for changed paths.

    Empty input and unknown paths intentionally select the broad fallback.  A caller may never
    turn an incomplete diff into a successful no-op by accident.
    """

    normalized = tuple(dict.fromkeys(path for path in paths if path))
    capabilities: set[str] = set()
    reasons: dict[str, str] = {}
    admin_specs: set[str] = set()
    full_reason: str | None = None
    web_scope: str | None = None
    admin_requires_full_suite = False
    fallback = ("full-baseline",) if heavy else BROAD_FALLBACK

    if not normalized:
        full_reason = "empty change scope; run the full affected baseline"

    for path in normalized:
        if _is_instruction_only_path(path):
            _add_capabilities(
                capabilities,
                reasons,
                ("docs",),
                "agent instructions require validation but no product test lane",
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
                    ("gate-tooling",),
                    "gate tooling changes require only the focused gate-tooling profile",
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
                ("python-unit", "backend-integration"),
                "integration test change requires the PostgreSQL-backed integration lane",
            )
            continue
        if path in GATE_TOOLING_TESTS:
            _add_capabilities(
                capabilities,
                reasons,
                ("gate-tooling",),
                "gate-tooling test changes require only the focused gate-tooling profile",
            )
            continue
        if path.startswith("tests/unit/"):
            _add_capabilities(
                capabilities,
                reasons,
                ("python-unit",),
                "test change requires the affected test lane",
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

    selected_capabilities = _capability_union(capabilities)
    if not selected_capabilities:
        raise RoutingError("routing selected zero capabilities")
    return GateSelection(
        paths=normalized,
        capabilities=selected_capabilities,
        reasons=reasons,
        admin_specs=tuple(sorted(admin_specs)),
        web_scope=web_scope or "all",
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


def _print_selection(
    selection: GateSelection,
    *,
    json_output: bool,
    capabilities_only: bool,
    admin_specs_only: bool,
    paths_only: bool,
    web_scope_only: bool,
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
    for name in selection.capabilities:
        if capabilities_only:
            print(name)
        else:
            print(f"capability: {name}")
            print(f"reason: {selection.reasons[name]}")
    if not capabilities_only:
        for name in CAPABILITY_ORDER:
            if name not in selection.capabilities and name in selection.reasons:
                reason = selection.reasons[name]
                if reason.startswith("deferred to candidate qualification:"):
                    print(f"reason: {reason}")
    if selection.admin_specs and not capabilities_only:
        print("admin-specs: " + " ".join(selection.admin_specs))


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
                )
            )
            > 1
        ):
            parser.error("choose one selection output channel")
        try:
            paths = changed_paths(
                staged=args.staged,
                dirty=args.dirty,
                base=args.base,
                head=args.head,
            )
            _print_selection(
                select_capabilities(paths, heavy=(args.dirty or args.staged) and not args.commit),
                json_output=args.json,
                capabilities_only=args.capabilities_only,
                admin_specs_only=args.admin_specs_only,
                paths_only=args.paths_only,
                web_scope_only=args.web_scope_only,
            )
        except (RoutingError, subprocess.CalledProcessError) as exc:
            print(f"gate routing failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return 0
    parser.error("--integration is required")


if __name__ == "__main__":
    raise SystemExit(main())
