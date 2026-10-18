"""Claude CLI routing, sandbox construction, and stream validation (NAD-528, ADR 0052).

Only runs whose coordinator is Claude Code reach this module. Codex-coordinated runs never read
the routing file, and without ``--coordinator claude-code`` callers keep today's Codex argv and
environment untouched.
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTING_FILE = PROJECT_ROOT / ".claude" / "role-models.toml"
ROLES_DIR = PROJECT_ROOT / ".codex" / "agents"
# The oldest CLI the NAD-528 live probes (C1-C5) verified; 2.1.146 drops Grep/Glob from --tools.
MIN_CLAUDE_VERSION = "2.1.302"
# Variables Codex 0.159 exports into its shell commands (NAD-528 probe C6); any one of them means
# the caller runs inside a Codex session. CODEX_SESSION_ID/THREAD_ID are set even unsandboxed.
CODEX_SESSION_MARKERS = (
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
)
CLAUDE_MODEL = re.compile(r"claude-[a-z0-9-]+\Z")
EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
WORKFLOW_ROUTES = frozenset({"Small", "Standard", "High-risk"})
# Plans write `**Workflow route:** X`, `**Workflow route: X**` or plain `Workflow route: X`.
ROUTE_LINE = re.compile(
    r"^[ \t]*(?:\*\*)?Workflow route:(?:\*\*)?[ \t]*([^*\n]*?)[ \t]*(?:\*\*)?[ \t]*$",
    re.MULTILINE,
)
# Spec D-002: these roles stay on Codex whatever the coordinator.
D002_ROLES = frozenset(
    {
        "backend_qa",
        "frontend_qa",
        "reviewer",
        "small_product_reviewer",
        "standard_plan_reviewer",
        "claim_researcher",
    }
)
# Spec D-001: the only roles the routing file may name.
MAPPED_ROLES = frozenset(
    {"executor", "code_explorer", "focused_backend_qa", "focused_frontend_qa", "architect"}
)
DENIED_AGENT_TOOLS = ("Agent", "Task", "WebFetch", "WebSearch", "NotebookEdit")
# Command prefixes are defence in depth only; the OS sandbox is the real boundary.
DENIED_BASH = (
    "Bash(claude *)",
    "Bash(codex *)",
    "Bash(git commit *)",
    "Bash(git push *)",
    "Bash(git reset --hard *)",
    "Bash(scripts/codex_role_dispatch.py *)",
    "Bash(.claude/skills/codex-*/scripts/*)",
)
DENIED_TOOLS = (*DENIED_AGENT_TOOLS, "mcp__*", *DENIED_BASH)
WRITE_TOOLS = frozenset({"Bash", "Edit", "Write"})
FILE_EDIT_TOOLS = frozenset({"Edit", "Write"})
GIT_METADATA = (".git", ".gitmodules")
DOCKER_SOCKET_CANDIDATES = (Path("/var/run/docker.sock"), Path("~/.docker/run/docker.sock"))
TOOL_PROFILES = {
    "read-only": ("Read", "Grep", "Glob"),
    "workspace-write": ("Read", "Grep", "Glob", "Bash", "Edit", "Write"),
}
_IMPLEMENTER_KEYS = frozenset({"provider", "model", "effort", "high_risk_model"})
_ROLE_KEYS = frozenset({"provider", "model", "effort"})


class ClaudeTransportError(Exception):
    """A safe, user-facing Claude transport refusal."""


@dataclass(frozen=True)
class Route:
    model: str
    effort: str


@dataclass(frozen=True)
class RoutingMap:
    """Validated Claude routing: per-role routes plus the implementer's route pair."""

    roles: Mapping[str, Route]
    implementer: Route | None
    implementer_high_risk: Route | None

    def route_for(self, workflow_route: str) -> Route:
        if self.implementer is None or self.implementer_high_risk is None:
            raise ClaudeTransportError("the routing file has no [implementer] entry")
        return self.implementer_high_risk if workflow_route == "High-risk" else self.implementer


def _route(table: Mapping[str, object], label: str) -> Route:
    if table.get("provider") != "claude":
        raise ClaudeTransportError(f"invalid routing key {label}.provider")
    model = table.get("model")
    effort = table.get("effort")
    if not isinstance(model, str) or CLAUDE_MODEL.fullmatch(model) is None:
        raise ClaudeTransportError(f"invalid routing key {label}.model")
    if not isinstance(effort, str) or effort not in EFFORTS:
        raise ClaudeTransportError(f"invalid routing key {label}.effort")
    return Route(model, effort)


def load_routes(path: Path = ROUTING_FILE, *, roles_dir: Path = ROLES_DIR) -> RoutingMap | None:
    """Load the Claude-only map strictly. ``None`` (no file) means every role stays on Codex."""
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ClaudeTransportError(f"invalid routing file {path}: {type(exc).__name__}") from exc
    # Removing entries is the rollback: a missing [implementer] or [roles] means Codex for them.
    if not set(data) <= {"schema", "implementer", "roles"} or data.get("schema") != 1:
        raise ClaudeTransportError("invalid routing file key schema")
    implementer_table = data.get("implementer")
    roles_table = data.get("roles", {})
    if not isinstance(roles_table, dict):
        raise ClaudeTransportError("invalid routing key roles")
    implementer: Route | None = None
    high_risk: Route | None = None
    if implementer_table is not None:
        if not isinstance(implementer_table, dict) or set(implementer_table) != _IMPLEMENTER_KEYS:
            raise ClaudeTransportError("invalid routing key implementer")
        implementer = _route(implementer_table, "implementer")
        high_model = implementer_table["high_risk_model"]
        if not isinstance(high_model, str) or CLAUDE_MODEL.fullmatch(high_model) is None:
            raise ClaudeTransportError("invalid routing key implementer.high_risk_model")
        high_risk = Route(high_model, implementer.effort)

    existing = {item.stem for item in roles_dir.glob("*.toml")}
    roles: dict[str, Route] = {}
    for name in sorted(roles_table):
        if name in D002_ROLES:
            raise ClaudeTransportError(f"D-002 role is forbidden in routing file: {name}")
        if name not in existing:
            raise ClaudeTransportError(f"routing role does not exist: {name}")
        if name not in MAPPED_ROLES:
            raise ClaudeTransportError(f"routing role is not in D-001: {name}")
        config = roles_table[name]
        if not isinstance(config, dict):
            raise ClaudeTransportError(f"invalid routing key roles.{name}")
        if name == "executor":
            if config != {"use": "implementer"} or implementer is None:
                raise ClaudeTransportError(
                    'roles.executor must be {use = "implementer"} with an [implementer] entry'
                )
            roles[name] = implementer
            continue
        if set(config) != _ROLE_KEYS:
            raise ClaudeTransportError(f"invalid routing key roles.{name}")
        roles[name] = _route(config, f"roles.{name}")
    return RoutingMap(roles=roles, implementer=implementer, implementer_high_risk=high_risk)


def resolve_route(contract: Path, routing: RoutingMap) -> tuple[str, Route]:
    """Resolve the implementer from exactly one route line; there is no default (spec D-005)."""
    remedy = (
        f"{contract}: add exactly one `**Workflow route:** Small|Standard|High-risk` line "
        "classified under .claude/skills/nadili-process/proportional-workflows.md"
    )
    try:
        text = contract.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ClaudeTransportError(remedy) from exc
    matches = ROUTE_LINE.findall(text)
    if len(matches) != 1 or matches[0] not in WORKFLOW_ROUTES:
        raise ClaudeTransportError(remedy)
    return matches[0], routing.route_for(matches[0])


def check_runtime_signal(coordinator: str, environment: Mapping[str, str] | None = None) -> None:
    """Accept ``claude-code`` only from a Claude Code shell that is not inside a Codex session."""
    env = os.environ if environment is None else environment
    if coordinator != "claude-code":
        return
    if env.get("CLAUDECODE") != "1" or any(key in env for key in CODEX_SESSION_MARKERS):
        raise ClaudeTransportError(
            "--coordinator claude-code requires CLAUDECODE=1 and no Codex session marker"
        )


def scrub_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Drop Claude Code host variables; they break child auth and must not leak to children."""
    return {
        key: value
        for key, value in environment.items()
        if key != "CLAUDECODE"
        and (not key.startswith("CLAUDE_CODE_") or key == "CLAUDE_CODE_OAUTH_TOKEN")
    }


def child_environment(environment: Mapping[str, str], *, coordinator: str) -> dict[str, str]:
    """Scrub only for Claude-coordinated runs; the Codex path keeps today's environment."""
    if coordinator == "claude-code":
        return scrub_environment(environment)
    return dict(environment)


def absolute_rule_path(path: Path) -> str:
    """Spell an absolute path the way Claude settings and permission rules require: ``//abs``.

    A single leading slash means "relative to the settings file" there. ``//abs`` is also the
    same path to POSIX, so the spelling is safe wherever a raw path is expected.
    """
    return "/" + str(path.resolve())


def literal_rule_path(path: Path) -> str:
    """Spell an already absolute path as ``//abs`` without following a symlink at its leaf."""
    if not path.is_absolute():
        raise ClaudeTransportError(f"rule path must be absolute: {path}")
    return "/" + str(path)


def git_metadata_paths(worktree: Path) -> tuple[Path, ...]:
    """The worktree's git metadata, kept read-only as Codex workspace-write keeps it.

    A sandboxed child that rewrote a linked worktree's ``.git`` file could point it at a fake
    gitdir with hooks, which the coordinator's next unsandboxed ``git commit`` would run. A
    symlinked entry is denied both as the link and as its target.
    """
    root = worktree.resolve()
    paths: list[Path] = []
    for name in GIT_METADATA:
        for path in (root / name, (root / name).resolve()):
            if path not in paths:
                paths.append(path)
    return tuple(paths)


def docker_sockets() -> list[str]:
    """Existing local Docker daemon sockets (symlink and target both), for QA roles only."""
    sockets: list[str] = []
    for candidate in DOCKER_SOCKET_CANDIDATES:
        path = candidate.expanduser()
        if path.exists():
            for item in (path, path.resolve()):
                if str(item) not in sockets:
                    sockets.append(str(item))
    return sockets


def build_settings(
    profile: str, worktree: Path, output_dir: Path, *, qa: bool = False
) -> dict[str, object]:
    """Build the ``--settings`` document (keys from the CLI's own settings schema).

    ``failIfUnavailable`` makes the CLI exit at startup when the OS sandbox cannot start; the
    run then never emits ``system/init`` and the runner reports ``claude_environment``.
    """
    if profile not in TOOL_PROFILES:
        raise ClaudeTransportError(f"unknown Claude tool profile {profile}")
    temp_root = Path(tempfile.gettempdir()).resolve()
    writable = [absolute_rule_path(worktree), absolute_rule_path(temp_root)]
    if temp_root.parts[:3] == ("/", "private", "var"):
        # macOS tools address TMPDIR both as /private/var/folders/... and /var/folders/...; the
        # alias names only this user's folder, never every user's /var/folders tree.
        writable.append(literal_rule_path(Path("/", *temp_root.parts[2:])))
    denied = [absolute_rule_path(output_dir)]
    denied.extend(literal_rule_path(path) for path in git_metadata_paths(worktree))
    network: dict[str, object] = {"allowedDomains": []}
    if qa:
        # Loopback services on any port, the local Docker daemon and the host browser server.
        network = {
            "allowedDomains": ["localhost", "127.0.0.1"],
            "allowLocalBinding": True,
            "allowUnixSockets": docker_sockets(),
        }
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {
                "allowWrite": writable,
                "denyWrite": denied,
            },
            "network": network,
        },
        "permissions": {"allow": [], "deny": list(DENIED_TOOLS)},
    }


def build_command(
    binary: Path,
    *,
    model: str,
    effort: str,
    profile: str,
    worktree: Path,
    settings_path: Path,
    system_prompt: str,
    session_args: tuple[str, ...] = ("--no-session-persistence",),
    protected: tuple[Path, ...] = (),
) -> list[str]:
    """Build ``claude -p``; the prompt itself goes in on stdin.

    ``protected`` paths (runner state inside the worktree) are denied to the edit tools, so a
    child can never rewrite the session or report files its parent publishes. The worktree's
    git metadata is always denied the same way.
    """
    tools = TOOL_PROFILES[profile]
    # A bare "Edit"/"Write" in the allowlist would grant every path; only scoped rules go there.
    allowed = [tool for tool in tools if tool not in FILE_EDIT_TOOLS]
    denied = list(DENIED_TOOLS)
    if profile == "workspace-write":
        root = absolute_rule_path(worktree)
        allowed.extend(f"{tool}({root}/**)" for tool in sorted(FILE_EDIT_TOOLS))
        scoped = [absolute_rule_path(path) for path in protected]
        scoped.extend(literal_rule_path(path) for path in git_metadata_paths(worktree))
        # The path itself covers a file such as a linked worktree's `.git`; `/**` its contents.
        for rule in scoped:
            denied.extend(
                f"{tool}({pattern})"
                for tool in sorted(FILE_EDIT_TOOLS)
                for pattern in (rule, f"{rule}/**")
            )
    return [
        str(binary),
        "-p",
        "--model",
        model,
        "--effort",
        effort,
        "--output-format",
        "stream-json",
        "--verbose",
        "--append-system-prompt",
        system_prompt,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--setting-sources",
        "",
        "--settings",
        str(settings_path),
        "--disable-slash-commands",
        "--permission-mode",
        "dontAsk",
        "--tools",
        ",".join(tools),
        "--allowedTools",
        ",".join(allowed),
        "--disallowedTools",
        ",".join(denied),
        *session_args,
    ]


def resolve_binary(explicit: Path | None) -> Path:
    """``--claude-bin``, then ``NADILI_CLAUDE_BIN``, then ``claude`` on PATH."""
    if explicit is not None:
        return explicit
    configured = os.environ.get("NADILI_CLAUDE_BIN")
    if configured:
        return Path(configured)
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / "claude"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ClaudeTransportError("claude_environment: claude executable was not found on PATH")


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def preflight(binary: Path, minimum: str = MIN_CLAUDE_VERSION) -> str:
    """Refuse a CLI older than the live-verified minimum before any model call."""
    try:
        result = subprocess.run(  # noqa: S603 -- explicit CLI path and fixed version argument.
            [str(binary), "--version"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaudeTransportError(
            f"claude_environment: Claude CLI preflight failed for {binary}; update Claude Code"
        ) from exc
    version = re.search(r"\b(\d+\.\d+\.\d+)\b", result.stdout + result.stderr)
    found = version.group(1) if version else "unknown"
    if result.returncode != 0 or version is None or _version_tuple(found) < _version_tuple(minimum):
        raise ClaudeTransportError(
            f"claude_environment: Claude CLI {binary} version {found} is unsupported; "
            f"run `claude update` or pass --claude-bin with {minimum} or newer"
        )
    return found


def validate_init(event: Mapping[str, object], *, model: str, profile: str) -> None:
    """Trust a run only if the CLI reports exactly the requested model and tool surface."""
    if (
        event.get("model") != model
        or event.get("permissionMode") != "dontAsk"
        or event.get("mcp_servers") != []
    ):
        raise ClaudeTransportError("claude_environment: Claude init settings mismatch")
    tools = event.get("tools")
    if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
        raise ClaudeTransportError("claude_environment: Claude init tool profile mismatch")
    names = set(cast(list[str], tools))
    if not set(TOOL_PROFILES[profile]) <= names:
        raise ClaudeTransportError("claude_environment: Claude init tool profile mismatch")
    if names & set(DENIED_AGENT_TOOLS) or any(name.startswith("mcp__") for name in names):
        raise ClaudeTransportError("claude_environment: Claude init contains a denied tool")
    if profile == "read-only" and names & WRITE_TOOLS:
        raise ClaudeTransportError("claude_environment: Claude init tool profile mismatch")


class StreamTimeout(Exception):
    """The Claude run outlived its deadline; the caller's cleanup stops its process group."""


def follow_stream(
    process: subprocess.Popen[bytes],
    event_log: BinaryIO,
    *,
    deadline: float | None,
    model: str,
    profile: str,
) -> int:
    """Copy stream-json to ``event_log``; stop trusting the run at a bad ``system/init``.

    Raises ``ClaudeTransportError`` on an init mismatch and ``StreamTimeout`` past ``deadline``
    (``None`` = no deadline). Either way the caller stops the process group.
    """
    if process.stdout is None:
        raise ClaudeTransportError("claude_environment: no stream-json stdout")
    descriptor = process.stdout.fileno()
    pending = b""
    while True:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise StreamTimeout
        wait = 5.0 if remaining is None else min(5.0, remaining)
        readable, _, _ = select.select([descriptor], [], [], wait)
        if not readable:
            continue
        chunk = os.read(descriptor, 65_536)
        if not chunk:
            break
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:
            event_log.write(line + b"\n")
            event_log.flush()
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if is_init_event(event):
                validate_init(event, model=model, profile=profile)
    if pending:
        event_log.write(pending)
    # Closing stdout never extends the absolute deadline.
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        raise StreamTimeout
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        raise StreamTimeout from None


def is_init_event(event: object) -> bool:
    return (
        isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init"
    )


def _normalized_usage(raw: object) -> dict[str, int] | None:
    """Map Claude usage onto the ledger's Codex shape, where input includes cached tokens."""
    if not isinstance(raw, dict):
        return None
    fields = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
    values = [raw.get(field, 0) for field in fields]
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return None
    counts = cast(list[int], values)
    if any(value < 0 for value in counts):
        return None
    fresh, created, cached, output = counts
    return {
        "input_tokens": fresh + created + cached,
        "cached_input_tokens": cached,
        "output_tokens": output,
    }


@dataclass(frozen=True)
class StreamResult:
    report: str
    usage: dict[str, int] | None
    error_code: str | None
    session_id: str | None
    claude_code_version: str | None
    total_cost_usd: float | None


def parse_stream(events: Path, *, model: str, profile: str) -> StreamResult:
    """Extract the final report and usage; classify failures without retaining raw events."""
    report = ""
    usage: dict[str, int] | None = None
    error_code: str | None = None
    session_id: str | None = None
    version: str | None = None
    cost: float | None = None
    saw_init = False
    saw_result = False
    text = events.read_text(encoding="utf-8", errors="replace") if events.is_file() else ""
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if is_init_event(event):
            saw_init = True
            init = cast(dict[str, object], event)
            session_value = init.get("session_id")
            version_value = init.get("claude_code_version")
            session_id = session_value if isinstance(session_value, str) else None
            version = version_value if isinstance(version_value, str) else None
            try:
                validate_init(init, model=model, profile=profile)
            except ClaudeTransportError:
                error_code = "claude_environment"
        elif isinstance(event, dict) and event.get("type") == "result":
            saw_result = True
            raw_result = event.get("result")
            report = raw_result if isinstance(raw_result, str) else ""
            raw_cost = event.get("total_cost_usd")
            cost = float(raw_cost) if isinstance(raw_cost, int | float) else None
            usage = _normalized_usage(event.get("usage"))
            if event.get("is_error") is True and error_code is None:
                # The CLI reports auth failure as subtype "success" with is_error.
                auth = re.search(r"\b401\b|oauth|authenticat", report, re.IGNORECASE)
                error_code = "claude_auth_failed" if auth else "claude_failed"
    if error_code is None and not saw_init:
        error_code = "claude_environment"
    if error_code is None and (not saw_result or not report.strip()):
        error_code = "missing_result"
    return StreamResult(report, usage, error_code, session_id, version, cost)
