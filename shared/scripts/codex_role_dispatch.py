#!/usr/bin/env python3
"""Run one ``.codex/agents/<role>.toml`` role as a fresh, one-shot ``codex exec``.

Claude Code cannot open native Codex agent threads. Without this runner every
Nadili role (QA testers, plan reviewers, architect, explorer, executor) became a
Claude subagent on the coordinator's expensive model. The runner applies the same
role file Codex uses natively -- model, effort, sandbox, tool surface and developer
instructions -- so the role contract keeps a single source. Codex sessions keep
using their native roles; nothing here changes them.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import os
import re
import select
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import claude_transport

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLES_DIR = PROJECT_ROOT / ".codex" / "agents"
PROJECT_CODEX_CONFIG = PROJECT_ROOT / ".codex" / "config.toml"
BUDGET_SCRIPT = PROJECT_ROOT / "scripts" / "codex_orchestration_budget.py"
BROWSER_SERVER = PROJECT_ROOT / "scripts" / "codex_qa_browser_server.mjs"

ROLE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
ACTION_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
ITEM_ID = re.compile(r"NAD-\d{1,6}")
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
SANDBOX_MODES = frozenset({"read-only", "workspace-write"})
MAX_BRIEF_BYTES = 64_000
BROWSER_START_SECONDS = 60.0
ENDPOINT_MAX_BYTES = 4096
GROUP_GRACE_SECONDS = 10.0
MAX_REASON_CHARS = 200
EXIT_CANCELLED = 130
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_BUDGET = 3
# Claude-runtime refusals (signal, routing, missing workflow route) use the shell usage code.
EXIT_SIGNAL = 64
# Appended after the role's developer instructions. The run rules (one-shot, no questions,
# BLOCKED, report) live in build_prompt, shared with Codex; this adds only the Claude tool facts.
CLAUDE_RUNNER_PREAMBLE = (
    "Your tools are exactly those listed for this session. File edits, where allowed, are "
    "limited to the item worktree; shell commands run in a sandbox; agents, MCP servers, "
    "skills and web tools are unavailable."
)

# Role metadata and keys passed as dedicated CLI flags rather than config overrides.
# Plugins are disabled wholesale below, so per-plugin tables add nothing.
NON_CONFIG_ROLE_KEYS = frozenset(
    {"name", "description", "model", "model_reasoning_effort", "sandbox_mode", "plugins"}
)
ROLE_DEFAULTS = ('web_search="disabled"', 'model_verbosity="low"')
# Applied after the role's own keys so no role can reopen nesting, apps, MCP or plugins.
# One-shot dispatch has no owner in the loop, hence approval_policy="never".
HARDENING = (
    'approval_policy="never"',
    "agents.enabled=false",
    "features.multi_agent=false",
    "features.apps=false",
    "apps._default.enabled=false",
    "mcp_servers={}",
    "features.plugins=false",
    "features.recommended_plugins=false",
    "features.remote_plugin=false",
    "features.hooks=false",
    "features.image_generation=false",
    "features.computer_use=false",
    "features.browser_use=false",
    "features.browser_use_external=false",
    "features.in_app_browser=false",
)
PLAYWRIGHT_CANDIDATES = (
    Path("apps/admin-web/node_modules/@playwright/test"),
    Path("node_modules/@playwright/test"),
    Path("node_modules/playwright"),
)


class DispatchError(Exception):
    """Raised when a role cannot be dispatched as requested."""

    def __init__(self, message: str, *, exit_code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class Cancelled(BaseException):
    """A catchable termination signal; a BaseException so every ``finally`` cleanup runs."""


@dataclass(frozen=True)
class Role:
    name: str
    model: str
    effort: str
    sandbox: str
    config: tuple[str, ...]
    instructions: str


@dataclass(frozen=True)
class Ledger:
    item_id: str
    worktree: Path
    reservation_id: str
    attempt_id: str


def _toml_value(value: object, path: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        # A JSON string literal is a valid TOML basic string.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item, path) for item in value) + "]"
    if isinstance(value, dict) and not value:
        return "{}"
    raise DispatchError(f"role key {path} has an unsupported value type")


def flatten_config(table: Mapping[str, object], prefix: str = "") -> list[str]:
    """Render a role table as ``--config`` dotted overrides, leaves only."""
    overrides: list[str] = []
    for key, value in table.items():
        if not BARE_KEY.fullmatch(key):
            raise DispatchError(f"role key {key!r} cannot be passed as a dotted override")
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            overrides.extend(flatten_config(cast(Mapping[str, object], value), path))
        else:
            overrides.append(f"{path}={_toml_value(value, path)}")
    return overrides


def _project_sandbox() -> str | None:
    if not PROJECT_CODEX_CONFIG.is_file():
        return None
    with PROJECT_CODEX_CONFIG.open("rb") as handle:
        value = tomllib.load(handle).get("sandbox_mode")
    return value if isinstance(value, str) else None


def load_role(name: str, *, roles_dir: Path = ROLES_DIR) -> Role:
    if not ROLE_NAME.fullmatch(name):
        raise DispatchError("role name must be a lowercase .codex/agents file stem")
    path = roles_dir / f"{name}.toml"
    if not path.is_file():
        available = ", ".join(sorted(item.stem for item in roles_dir.glob("*.toml")))
        raise DispatchError(f"unknown role {name!r}; available: {available}")
    with path.open("rb") as handle:
        table = tomllib.load(handle)
    model = table.get("model")
    effort = table.get("model_reasoning_effort")
    if not isinstance(model, str) or not isinstance(effort, str):
        raise DispatchError(f"role {name} must pin model and model_reasoning_effort")
    instructions = table.get("developer_instructions")
    if not isinstance(instructions, str):
        raise DispatchError(f"role {name} has no developer_instructions")
    # Native roles without sandbox_mode inherit the project session setting.
    sandbox = table.get("sandbox_mode") or _project_sandbox() or "read-only"
    if sandbox not in SANDBOX_MODES:
        raise DispatchError(f"role {name} requests unsupported sandbox {sandbox!r}")
    role_config = {key: value for key, value in table.items() if key not in NON_CONFIG_ROLE_KEYS}
    return Role(
        name=name,
        model=model,
        effort=effort,
        sandbox=cast(str, sandbox),
        config=tuple(flatten_config(role_config)),
        instructions=instructions,
    )


def build_command(
    codex: Path,
    role: Role,
    *,
    worktree: Path,
    model: str,
    effort: str,
    network: bool,
    final_message: Path,
) -> list[str]:
    overrides = [*ROLE_DEFAULTS, *role.config, f'model_reasoning_effort="{effort}"', *HARDENING]
    if network:
        overrides.append("sandbox_workspace_write.network_access=true")
    # No --strict-config: native role loading tolerates keys an installed Codex no longer
    # knows (executor.toml pins tools.view_image), and parity with it is the contract here.
    command = [
        str(codex),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--sandbox",
        role.sandbox,
        "--model",
        model,
        "--color",
        "never",
        "--cd",
        str(worktree),
    ]
    for override in overrides:
        command.extend(("--config", override))
    command.extend(("--json", "--output-last-message", str(final_message), "-"))
    return command


def build_prompt(
    role: Role,
    brief: str,
    *,
    network: bool,
    browser_ws: str | None,
    playwright_module: Path | None,
    transport: str = "codex",
) -> str:
    runner = "`codex exec`" if transport == "codex" else "`claude -p`"
    sections = [
        f"You are the Nadili `{role.name}` role. A Claude Code coordinator dispatched you through "
        f"{runner} as a fresh-context (fork_turns=none), one-shot run; your developer "
        f"instructions are the role contract in `.codex/agents/{role.name}.toml`. There is no "
        "follow-up turn and no one can answer questions: complete the bounded task below, write "
        "only the evidence paths it assigns, and end with your complete report as the final "
        "message. If a prerequisite is missing, report BLOCKED with evidence instead of guessing. "
        "You cannot spawn agents. Treat implementation summaries in the brief as claims, not proof.",
    ]
    if network:
        sections.append(
            "Runtime: shell commands may reach loopback services and the local Docker daemon. "
            "Stop every helper process you start before you finish."
        )
    if browser_ws is not None and playwright_module is not None:
        sections.append(
            "Browser: Chromium cannot start inside this sandbox, so never call "
            "`chromium.launch()`. A headless Chromium is already running outside it; connect "
            "with Node: `const { chromium } = require(process.env.NADILI_QA_PLAYWRIGHT_MODULE); "
            "const browser = await chromium.connect(process.env.NADILI_QA_BROWSER_WS);` "
            f"(module `{playwright_module}`). Close your pages and contexts when done; "
            "the coordinator stops the server."
        )
    sections.append("## Handoff brief\n\n" + brief.strip())
    return "\n\n".join(sections) + "\n"


def find_playwright(worktree: Path) -> Path:
    for candidate in PLAYWRIGHT_CANDIDATES:
        path = worktree / candidate
        if (path / "package.json").is_file():
            return path.resolve()
    raise DispatchError(
        "no Playwright module in the worktree; run `CI=true pnpm install` there "
        "or pass --playwright-module"
    )


def parse_usage(events: Path) -> dict[str, int] | None:
    """Return the last ``turn.completed`` usage from a ``codex exec --json`` stream."""
    usage: dict[str, int] | None = None
    if not events.is_file():
        return None
    with events.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "turn.completed":
                continue
            raw = event.get("usage")
            if not isinstance(raw, dict):
                continue
            fields = ("input_tokens", "cached_input_tokens", "output_tokens")
            values = {field: raw.get(field) for field in fields}
            if all(isinstance(value, int) and value >= 0 for value in values.values()):
                usage = cast(dict[str, int], values)
    return usage


def _budget_output(ledger: Ledger, operation: str, *arguments: str) -> str:
    command = [
        sys.executable,
        str(BUDGET_SCRIPT),
        operation,
        "--worktree",
        str(ledger.worktree),
        "--item-id",
        ledger.item_id,
        *arguments,
    ]
    try:
        # A separate session keeps terminal Ctrl-C from killing the ledger between committing
        # an attempt and acknowledging it; the dispatcher defers its own cancellation instead.
        result = subprocess.run(  # noqa: S603 -- fixed project-local budget command.
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
            start_new_session=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DispatchError(f"budget {operation} could not run", exit_code=EXIT_BUDGET) from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["no diagnostic"]
        raise DispatchError(f"budget {operation} refused: {detail[0]}", exit_code=EXIT_BUDGET)
    return result.stdout


def _budget(ledger: Ledger, operation: str, *arguments: str) -> dict[str, object]:
    output = _budget_output(ledger, operation, *arguments)
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise DispatchError(f"budget {operation} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise DispatchError(f"budget {operation} returned a non-object result")
    return cast(dict[str, object], payload)


def _open_ledger(
    ledger: Ledger,
    *,
    stage: str,
    model: str,
    effort: str,
    attempt: int,
    reason: str,
    transport: str = "codex",
) -> None:
    state_path = _budget_output(ledger, "state-path").strip()
    if not state_path or not Path(state_path).is_absolute():
        raise DispatchError("cannot resolve the item budget ledger", exit_code=EXIT_BUDGET)
    if not Path(state_path).exists():
        _budget(ledger, "init")
    reserved = _budget(
        ledger,
        "reserve",
        "--id",
        ledger.reservation_id,
        "--kind",
        "agent",
        "--agent-id",
        f"{('claude-p' if transport == 'claude' else 'codex-exec')}-{ledger.reservation_id}",
        "--fork-turns",
        "none",
        "--stage",
        stage,
        "--model",
        model,
        "--effort",
        effort,
        "--category",
        "model",
        "--reason",
        reason,
    )
    if reserved.get("alerts"):
        print(f"budget alerts: {json.dumps(reserved['alerts'])}", file=sys.stderr)
    start_arguments = ["--reservation-id", ledger.reservation_id, "--attempt-id", ledger.attempt_id]
    if attempt > 1:
        start_arguments.extend(("--retry-of", f"{ledger.reservation_id}-attempt-{attempt - 1}"))
    started = _budget(ledger, "start", *start_arguments)
    if started.get("created") is not True:
        raise DispatchError(
            f"attempt {ledger.attempt_id} already exists; pass the next --attempt",
            exit_code=EXIT_BUDGET,
        )


def _close_ledger(
    ledger: Ledger,
    *,
    status: str,
    result_code: str,
    elapsed_ms: int,
    usage: dict[str, int] | None,
    transport: str = "codex",
) -> None:
    arguments = [
        "--attempt-id",
        ledger.attempt_id,
        "--status",
        status,
        "--result-code",
        result_code,
        "--elapsed-ms",
        str(elapsed_ms),
    ]
    if usage is None:
        arguments.extend(("--usage-source", "unavailable"))
    else:
        arguments.extend(
            (
                "--usage-source",
                "claude_event_stream" if transport == "claude" else "codex_event_stream",
                "--usage-channel",
                "stdout_jsonl",
                "--input-tokens",
                str(usage["input_tokens"]),
                "--cached-input-tokens",
                str(usage["cached_input_tokens"]),
                "--output-tokens",
                str(usage["output_tokens"]),
            )
        )
    try:
        _budget(ledger, "finish", *arguments)
    except DispatchError as exc:
        # The run already happened; report the bookkeeping gap instead of hiding the result.
        print(f"warning: {exc}; reconcile {ledger.attempt_id} manually", file=sys.stderr)


class _CancellationGate:
    """Turn termination signals into ``Cancelled``, deferred across ownership handovers.

    Between creating a child (or committing a ledger attempt) and registering it for cleanup
    a raised signal would orphan it. Inside ``deferred()`` a signal is only recorded and then
    raised once the handover is complete, so cleanup always sees what was started.
    """

    def __init__(self) -> None:
        self.deferring = False
        self.pending: str | None = None

    def handler(self, signum: int, _frame: object) -> None:
        name = signal.Signals(signum).name
        if self.deferring:
            self.pending = self.pending or name
            return
        raise Cancelled(name)

    @contextlib.contextmanager
    def deferred(self) -> Iterator[None]:
        self.deferring = True
        try:
            yield
        finally:
            # A signal deferred while another error propagates is dropped: that error already
            # ends the run through the same cleanup.
            self.deferring = False
            pending, self.pending = self.pending, None
        if pending is not None:
            raise Cancelled(pending)


CANCELLATION = _CancellationGate()
CANCEL_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@contextlib.contextmanager
def _shielded() -> Iterator[None]:
    """Keep a second signal from abandoning cleanup halfway."""
    previous = {signum: signal.signal(signum, signal.SIG_IGN) for signum in CANCEL_SIGNALS}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The id now belongs to another user's group; nothing of ours remains in it.
        return False
    return True


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    """Stop a run's whole session, including descendants that outlive or ignore the leader."""
    pgid = process.pid
    for signum in (signal.SIGTERM, signal.SIGKILL):
        # macOS answers EPERM for a group whose only member is an unreaped zombie leader.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signum)
        # Killed descendants linger briefly as zombies until init reaps them.
        deadline = time.monotonic() + GROUP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None and not _group_alive(pgid):
                return
            time.sleep(0.05)
    print(f"warning: process group {pgid} survived SIGKILL", file=sys.stderr)


def _start_browser(
    playwright_module: Path,
    log: Path,
    owned: list[subprocess.Popen[bytes]],
    environment: dict[str, str] | None = None,
) -> str:
    """Start the host browser server; the caller's cleanup owns it from the moment it exists."""
    node = _which("node")
    with log.open("wb") as stderr, CANCELLATION.deferred():
        process = subprocess.Popen(  # noqa: S603 -- resolved node and fixed project script.
            [str(node), str(BROWSER_SERVER), str(playwright_module)],
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=environment,
            start_new_session=True,
        )
        owned.append(process)
    if process.stdout is None:
        raise DispatchError("browser server has no stdout", exit_code=EXIT_FAILED)
    descriptor = process.stdout.fileno()
    deadline = time.monotonic() + BROWSER_START_SECONDS
    buffer = b""
    while b"\n" not in buffer and len(buffer) <= ENDPOINT_MAX_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DispatchError(
                f"browser server reported no endpoint within {BROWSER_START_SECONDS:.0f}s; "
                f"see {log}",
                exit_code=EXIT_FAILED,
            )
        readable, _, _ = select.select([descriptor], [], [], remaining)
        if not readable:
            continue
        chunk = os.read(descriptor, 1024)
        if not chunk:
            break
        buffer += chunk
    try:
        endpoint = json.loads(buffer.split(b"\n", 1)[0])["wsEndpoint"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as exc:
        raise DispatchError(
            f"browser server did not start; see {log}", exit_code=EXIT_FAILED
        ) from exc
    if not isinstance(endpoint, str) or not endpoint.startswith("ws://127.0.0.1:"):
        raise DispatchError("browser server returned an unexpected endpoint", exit_code=EXIT_FAILED)
    return endpoint


def _which(name: str) -> Path:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise DispatchError(f"{name} executable was not found on PATH")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", required=True, help="stem of a .codex/agents/*.toml file")
    parser.add_argument("--worktree", type=Path, required=True, help="absolute item worktree")
    parser.add_argument("--brief", type=Path, required=True, help="bounded handoff brief file")
    parser.add_argument("--item-id", help="NAD-<n>; records the run in the item budget ledger")
    parser.add_argument("--action-id", help="opaque ledger reservation id, e.g. backend-qa-1")
    parser.add_argument("--stage", help="ledger stage, e.g. backend-qa or plan-review")
    parser.add_argument("--attempt", type=int, default=1, help="retry of a failed run only")
    parser.add_argument("--model", help="explicit model override (needs --override-reason)")
    parser.add_argument("--effort", help="explicit effort override (needs --override-reason)")
    parser.add_argument("--override-reason", help="named risk that justifies the override")
    parser.add_argument("--network", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--browser", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--playwright-module", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=3600, help="seconds; default 3600")
    parser.add_argument("--codex-bin", type=Path)
    parser.add_argument("--claude-bin", type=Path)
    parser.add_argument("--coordinator", choices=("codex", "claude-code"), default="codex")
    parser.add_argument("--transport", choices=("auto", "codex", "claude"), default="auto")
    parser.add_argument("--dry-run", action="store_true", help="print the command, run nothing")
    return parser


def _ledger_from_args(args: argparse.Namespace, worktree: Path) -> Ledger | None:
    if args.item_id is None:
        if args.action_id is not None or args.stage is not None:
            raise DispatchError("--action-id and --stage require --item-id")
        return None
    if not ITEM_ID.fullmatch(args.item_id):
        raise DispatchError("--item-id must look like NAD-123")
    if args.action_id is None or not ACTION_ID.fullmatch(args.action_id):
        raise DispatchError("--item-id needs a lowercase --action-id such as backend-qa-1")
    if args.stage is None or not ACTION_ID.fullmatch(args.stage):
        raise DispatchError("--item-id needs a lowercase --stage such as backend-qa")
    if args.attempt < 1:
        raise DispatchError("--attempt must be at least 1")
    return Ledger(
        item_id=args.item_id,
        worktree=worktree,
        reservation_id=args.action_id,
        attempt_id=f"{args.action_id}-attempt-{args.attempt}",
    )


def read_brief(path: Path) -> str:
    """Read at most the brief limit; never ingest an accidentally huge file."""
    if not path.is_file():
        raise DispatchError("--brief must be an existing file")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BRIEF_BYTES + 1)
    if len(raw) > MAX_BRIEF_BYTES:
        raise DispatchError(f"--brief must be at most {MAX_BRIEF_BYTES} bytes")
    try:
        brief = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DispatchError("--brief must be UTF-8 text") from exc
    if not brief.strip():
        raise DispatchError("--brief must not be empty")
    return brief


def override_reason(args: argparse.Namespace) -> str | None:
    has_override = args.model is not None or args.effort is not None
    reason = (args.override_reason or "").strip()
    if not has_override:
        if reason:
            raise DispatchError("--override-reason applies only with --model/--effort")
        return None
    if not reason or len(reason) > MAX_REASON_CHARS or "\n" in reason:
        raise DispatchError(
            f"--model/--effort overrides need a one-line --override-reason of at most "
            f"{MAX_REASON_CHARS} characters"
        )
    return reason


def reservation_reason(
    role: Role,
    *,
    model: str,
    effort: str,
    reason: str | None,
    transport: str = "codex",
) -> str:
    base = (
        f"claude-p-dispatch-{role.name}"
        if transport == "claude"
        else f"claude-code-dispatch-{role.name}"
    )
    return base if reason is None else f"{base}; override {model}/{effort}: {reason}"


def _budget_accepts_claude(worktree: Path) -> bool:
    """Inspect the item-owned enum without importing or executing its budget script."""
    path = worktree / "scripts" / "codex_orchestration_budget.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return False
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "USAGE_SOURCES" for target in node.targets
        ):
            return isinstance(node.value, ast.Set) and any(
                isinstance(value, ast.Constant) and value.value == "claude_event_stream"
                for value in node.value.elts
            )
    return False


@dataclass(frozen=True)
class ClaudeSelection:
    model: str
    effort: str
    route: str | None


def _executor_contract(worktree: Path, item_id: str) -> Path:
    plan = worktree / "docs" / "work" / item_id / "plan.md"
    return plan if plan.is_file() else plan.with_name("hotfix.md")


def select_claude(args: argparse.Namespace, role: Role, worktree: Path) -> ClaudeSelection | None:
    """Return the Claude route for this run, or ``None`` for today's Codex transport.

    Without ``--coordinator claude-code`` nothing here reads the routing file (spec D-003).
    """
    try:
        claude_transport.check_runtime_signal(args.coordinator)
    except claude_transport.ClaudeTransportError as exc:
        raise DispatchError(str(exc), exit_code=EXIT_SIGNAL) from exc
    forced = args.transport == "claude"
    if args.coordinator != "claude-code" or args.transport == "codex":
        if forced:
            raise DispatchError(
                "--transport claude requires --coordinator claude-code", exit_code=EXIT_SIGNAL
            )
        return None
    if role.name in claude_transport.D002_ROLES:
        if forced:
            raise DispatchError(
                f"role {role.name} stays on Codex (spec D-002)", exit_code=EXIT_SIGNAL
            )
        return None
    try:
        routing = claude_transport.load_routes()
    except claude_transport.ClaudeTransportError as exc:
        raise DispatchError(str(exc), exit_code=EXIT_SIGNAL) from exc
    if routing is None:
        print(
            f"warning: no Claude routing file at {claude_transport.ROUTING_FILE}; using Codex",
            file=sys.stderr,
        )
    route = routing.roles.get(role.name) if routing else None
    if route is None:
        if forced:
            raise DispatchError(f"role {role.name} has no Claude route", exit_code=EXIT_SIGNAL)
        return None
    route_name = None
    # An explicit --model with its recorded reason is the only way past the route line.
    if role.name == "executor" and args.model is None:
        if args.item_id is None:
            raise DispatchError(
                "the Claude executor needs --item-id to read the item's workflow route",
                exit_code=EXIT_SIGNAL,
            )
        try:
            route_name, route = claude_transport.resolve_route(
                _executor_contract(worktree, args.item_id),
                cast(claude_transport.RoutingMap, routing),
            )
        except claude_transport.ClaudeTransportError as exc:
            raise DispatchError(str(exc), exit_code=EXIT_SIGNAL) from exc
    model = args.model or route.model
    effort = args.effort or route.effort
    if claude_transport.CLAUDE_MODEL.fullmatch(model) is None:
        raise DispatchError(f"Claude transport needs a Claude model, got {model}", exit_code=2)
    if effort not in claude_transport.EFFORTS:
        raise DispatchError(f"Claude transport does not accept effort {effort}", exit_code=2)
    return ClaudeSelection(model=model, effort=effort, route=route_name)


def run(args: argparse.Namespace) -> int:
    worktree = cast(Path, args.worktree)
    if not worktree.is_absolute() or not worktree.is_dir():
        raise DispatchError("--worktree must be an existing absolute directory")
    worktree = worktree.resolve()
    brief = read_brief(cast(Path, args.brief))
    reason = override_reason(args)
    if args.timeout < 60:
        raise DispatchError("--timeout must be at least 60 seconds")

    role = load_role(args.role)
    ledger = _ledger_from_args(args, worktree)
    claude = select_claude(args, role, worktree)
    transport = "codex" if claude is None else "claude"
    model = claude.model if claude else args.model or role.model
    effort = claude.effort if claude else args.effort or role.effort
    if claude is not None and ledger is not None and not _budget_accepts_claude(worktree):
        raise DispatchError(
            "the item worktree's budget ledger does not accept claude_event_stream; "
            "rebase the item onto main first",
            exit_code=EXIT_SIGNAL,
        )
    network = role.name.endswith("_qa") if args.network is None else bool(args.network)
    browser = role.name.endswith("frontend_qa") if args.browser is None else bool(args.browser)
    if network and role.sandbox != "workspace-write":
        raise DispatchError(f"role {role.name} is {role.sandbox}; network needs workspace-write")
    if browser and not network:
        raise DispatchError("--browser needs --network: the tester reaches it over loopback")
    playwright_module = None
    if browser:
        playwright_module = (args.playwright_module or find_playwright(worktree)).resolve()
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    run_id = ledger.attempt_id if ledger else f"{role.name}-{stamp}"
    # Resolve once: Codex runs with cwd=worktree, so a relative path would point elsewhere.
    output_dir = (
        cast(Path | None, args.output_dir)
        or Path(tempfile.gettempdir()) / "nadili-codex-roles" / (args.item_id or "adhoc")
    ).resolve()
    if output_dir.is_relative_to(worktree):
        raise DispatchError("--output-dir must be outside the item worktree")
    final_message = output_dir / f"{run_id}.final.md"
    claude_version: str | None = None
    if transport == "codex":
        codex = args.codex_bin or _which("codex")
        command = build_command(
            codex,
            role,
            worktree=worktree,
            model=model,
            effort=effort,
            network=network,
            final_message=final_message,
        )
    else:
        try:
            binary = claude_transport.resolve_binary(args.claude_bin)
            claude_version = claude_transport.preflight(binary)
        except claude_transport.ClaudeTransportError as exc:
            raise DispatchError(str(exc), exit_code=EXIT_FAILED) from exc
        settings = claude_transport.build_settings(role.sandbox, worktree, output_dir, qa=network)
        settings_file = output_dir / f"{run_id}.settings.json"
        command = claude_transport.build_command(
            binary,
            model=model,
            effort=effort,
            profile=role.sandbox,
            worktree=worktree,
            settings_path=settings_file,
            system_prompt=f"{role.instructions}\n\n{CLAUDE_RUNNER_PREAMBLE}",
        )
    if args.dry_run:
        dry_run = {
            "role": role.name,
            "browser": browser,
            "override_reason": reason,
            "transport": transport,
        }
        print(json.dumps({**dry_run, "command": command}, indent=2))
        return 0

    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    events = output_dir / f"{run_id}.events.jsonl"
    stderr_log = output_dir / f"{run_id}.stderr.log"
    owned: list[subprocess.Popen[bytes]] = []
    ledger_started = False
    started = time.monotonic()
    status, result_code, exit_code = "failed", f"{transport}_failed", EXIT_FAILED
    usage: dict[str, int] | None = None
    claude_details: dict[str, object] = {}
    try:
        if ledger is not None:
            with CANCELLATION.deferred():
                _open_ledger(
                    ledger,
                    stage=args.stage,
                    model=model,
                    effort=effort,
                    attempt=args.attempt,
                    reason=reservation_reason(
                        role,
                        model=model,
                        effort=effort,
                        reason=reason,
                        transport=transport,
                    ),
                    transport=transport,
                )
                ledger_started = True
        # Only an admitted, newly started attempt may replace a previous report at this path.
        final_message.unlink(missing_ok=True)
        environment = claude_transport.child_environment(
            dict(os.environ), coordinator=args.coordinator
        )
        browser_ws = None
        if browser and playwright_module is not None:
            browser_ws = _start_browser(
                playwright_module, output_dir / f"{run_id}.browser.log", owned, environment
            )
            environment["NADILI_QA_BROWSER_WS"] = browser_ws
            environment["NADILI_QA_PLAYWRIGHT_MODULE"] = str(playwright_module)
        prompt_file = output_dir / f"{run_id}.prompt.md"
        prompt_file.write_text(
            build_prompt(
                role,
                brief,
                network=network,
                browser_ws=browser_ws,
                playwright_module=playwright_module,
                transport=transport,
            ),
            encoding="utf-8",
        )
        if transport == "claude":
            # Written by the parent before launch; the child's sandbox cannot modify it.
            settings_file.write_text(json.dumps(settings), encoding="utf-8")
            with (
                prompt_file.open("rb") as stdin,
                events.open("wb") as event_log,
                stderr_log.open("wb") as stderr,
            ):
                with CANCELLATION.deferred():
                    process = subprocess.Popen(  # noqa: S603 -- resolved Claude CLI and fixed arguments.
                        command,
                        stdin=stdin,
                        stdout=subprocess.PIPE,
                        stderr=stderr,
                        cwd=worktree,
                        env=environment,
                        start_new_session=True,
                    )
                    owned.append(process)
                try:
                    return_code = claude_transport.follow_stream(
                        process,
                        event_log,
                        deadline=time.monotonic() + args.timeout,
                        model=model,
                        profile=role.sandbox,
                    )
                except claude_transport.StreamTimeout:
                    status, result_code = "interrupted", "timeout"
                    raise DispatchError(
                        f"role run exceeded {args.timeout}s; see {stderr_log}",
                        exit_code=EXIT_FAILED,
                    ) from None
                except claude_transport.ClaudeTransportError as exc:
                    status, result_code = "failed", "claude_environment"
                    raise DispatchError(str(exc), exit_code=EXIT_FAILED) from exc
        else:
            with (
                prompt_file.open("rb") as stdin,
                events.open("wb") as stdout,
                stderr_log.open("wb") as stderr,
            ):
                with CANCELLATION.deferred():
                    process = subprocess.Popen(  # noqa: S603 -- resolved Codex CLI and fixed arguments.
                        command,
                        stdin=stdin,
                        stdout=stdout,
                        stderr=stderr,
                        cwd=worktree,
                        env=environment,
                        start_new_session=True,
                    )
                    owned.append(process)
                try:
                    return_code = process.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    status, result_code = "interrupted", "timeout"
                    raise DispatchError(
                        f"role run exceeded {args.timeout}s; see {stderr_log}",
                        exit_code=EXIT_FAILED,
                    ) from None
        if transport == "claude":
            stream = claude_transport.parse_stream(events, model=model, profile=role.sandbox)
            claude_details = {
                "claude_code_version": stream.claude_code_version or claude_version,
                "session_id": stream.session_id,
                "total_cost_usd": stream.total_cost_usd,
            }
            if stream.error_code is None and return_code == 0:
                # The parent, never the child, publishes the report.
                final_message.write_text(stream.report, encoding="utf-8")
                status, result_code, exit_code = "succeeded", "completed", 0
            else:
                result_code = stream.error_code or "claude_failed"
                if result_code == "claude_auth_failed":
                    print(
                        "Claude authentication failed: run `claude auth login`, or provide "
                        "CLAUDE_CODE_OAUTH_TOKEN, then retry. This is an environment failure: "
                        "do not escalate the model.",
                        file=sys.stderr,
                    )
        else:
            report = final_message.read_text(encoding="utf-8") if final_message.is_file() else ""
            if return_code == 0 and report.strip():
                status, result_code, exit_code = "succeeded", "completed", 0
            else:
                result_code = "empty_report" if return_code == 0 else "codex_failed"
    except (Cancelled, KeyboardInterrupt):
        status, result_code = "interrupted", "interrupted"
        raise
    finally:
        with _shielded():
            # Stop every owned session, finished or not: helpers may outlive the leader.
            for owned_process in reversed(owned):
                _stop_group(owned_process)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if transport == "codex":
                usage = parse_usage(events)
            elif events.is_file():
                usage = claude_transport.parse_stream(
                    events, model=model, profile=role.sandbox
                ).usage
            if ledger is not None and ledger_started:
                _close_ledger(
                    ledger,
                    status=status,
                    result_code=result_code,
                    elapsed_ms=elapsed_ms,
                    usage=usage,
                    transport=transport,
                )
    print(
        json.dumps(
            {
                "role": role.name,
                "transport": transport,
                "model": model,
                "effort": effort,
                "override_reason": reason,
                "sandbox": role.sandbox,
                "network": network,
                "browser": browser,
                "status": status,
                "result_code": result_code,
                "elapsed_ms": elapsed_ms,
                "usage": usage,
                "route": claude.route if claude else None,
                "final_message": str(final_message),
                "stderr_log": str(stderr_log),
                "ledger_attempt": ledger.attempt_id if ledger else None,
                **claude_details,
            },
            indent=2,
        )
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    previous = {signum: signal.signal(signum, CANCELLATION.handler) for signum in CANCEL_SIGNALS}
    try:
        return run(_parser().parse_args(argv))
    except DispatchError as exc:
        print(f"codex role dispatch: {exc}", file=sys.stderr)
        return exc.exit_code
    except (Cancelled, KeyboardInterrupt):
        print("codex role dispatch: cancelled; owned processes stopped", file=sys.stderr)
        return EXIT_CANCELLED
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
