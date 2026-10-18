#!/usr/bin/env python3
"""Persistent Claude implementer sessions behind codex-implement (NAD-528, ADR 0052).

The codex-implement wrappers exec this helper only after a leading ``--coordinator
claude-code``; without it they run today's Codex code unchanged. Claude state lives in its own
``<key>.claude-*`` files beside the Codex state, so no Codex reader ever sees it. One committed
record (``<key>.claude-session``) holds the session id and its latest report, published by a
single atomic rename, so a failed or interrupted run never leaves a half-published state.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import claude_transport

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".claude" / "skills" / "codex-implement" / "state"
CODEX_WRAPPERS = ROOT / ".claude" / "skills" / "codex-implement" / "scripts"
PROFILE = "workspace-write"
EXIT_FAILED = 1
EXIT_ARGS = 2
EXIT_STATE = 2
EXIT_USAGE = 64
EXIT_CANCELLED = 130
GROUP_GRACE_SECONDS = 10.0
CANCEL_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
# Only what differs from the Codex run; the task, scope and report rules come from the prompt
# template, exactly as for Codex.
RUNNER_CONTRACT = (
    "You run headless through `claude -p` in a sandbox, started by the codex-implement wrapper; "
    "nobody can answer questions mid-run, so record open questions in your report. File edits "
    "are limited to the working directory, shell commands have no network, and agents, MCP "
    "servers, skills and web tools are unavailable."
)


class ImplementError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class Cancelled(BaseException):
    """A termination signal; a BaseException so cleanup always runs."""


class _CancellationGate:
    """Turn signals into ``Cancelled``, deferred across child registration and commit points."""

    def __init__(self) -> None:
        self.deferring = False
        self.pending: str | None = None
        self.committed = False

    def handler(self, signum: int, _frame: object) -> None:
        if self.committed:
            return  # the result is published; cleanup and the success exit must finish
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
            self.deferring = False
            pending, self.pending = self.pending, None
        if pending is not None:
            raise Cancelled(pending)

    def commit(self, action: Callable[[], None]) -> None:
        """Run an atomic commit with one outcome per signal timing.

        A signal that arrived before the commit point aborts it (nothing is published). Once the
        commit point is entered the action completes and the run counts as success: signals
        during it or at any later point of the process are dropped, so cleanup and the exit 0
        always follow a published result.
        """
        self.deferring = True
        try:
            if self.pending is not None:
                raise Cancelled(self.pending)
            action()
            self.committed = True
        finally:
            self.deferring = False
            self.pending = None


CANCELLATION = _CancellationGate()


@contextlib.contextmanager
def _shielded() -> Iterator[None]:
    """Ignore further signals while cleanup stops the child group."""
    previous = {signum: signal.signal(signum, signal.SIG_IGN) for signum in CANCEL_SIGNALS}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def target_key(target: str) -> str:
    """Mirror ``target_key`` in codex-plan-review/scripts/_common.sh, including its cksum."""
    path = Path(target)
    resolved = str(path.resolve()) if path.exists() else target
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", resolved.removeprefix("/").replace("/", "__"))
    result = subprocess.run(  # noqa: S603 -- fixed POSIX tool; matches the shell derivation.
        ["/usr/bin/cksum"], input=resolved.encode(), capture_output=True, check=True
    )
    return f"{sanitized}.{result.stdout.split()[0].decode('ascii')}"


@dataclass(frozen=True)
class Artifacts:
    codex_thread: Path
    session: Path
    pending: Path
    events: Path
    lock: Path
    prompt: Path
    settings: Path

    @classmethod
    def for_target(cls, state: Path, target: str) -> Artifacts:
        key = target_key(target)

        def file(suffix: str) -> Path:
            return state / f"{key}{suffix}"

        return cls(
            codex_thread=file(".thread"),
            session=file(".claude-session"),
            pending=file(".claude-session.pending"),
            events=file(".claude-events.ndjson"),
            lock=file(".claude.lock"),
            prompt=file(".claude-prompt.md"),
            settings=file(".claude-settings.json"),
        )

    def removable(self) -> tuple[Path, ...]:
        # The lock is never unlinked: callers must keep contending on one inode.
        return (self.session, self.pending, self.events, self.prompt, self.settings)


@dataclass(frozen=True)
class Session:
    session_id: str
    report: str

    @classmethod
    def load(cls, path: Path) -> Session:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            return cls(session_id=str(record["session_id"]), report=str(record["report"]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ImplementError(
                f"unreadable Claude session record {path}; run reset.sh", EXIT_STATE
            ) from exc


def _stage(path: Path, text: str) -> Path:
    """Write ``text`` beside ``path`` for a later atomic ``os.replace``."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    return temporary


def _publish(path: Path, text: str) -> None:
    os.replace(_stage(path, text), path)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def _locked(artifacts: Artifacts) -> Iterator[None]:
    artifacts.lock.parent.mkdir(parents=True, exist_ok=True)
    with artifacts.lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ImplementError(
                "a Claude implementer run is active for this target; wait for it to finish",
                EXIT_STATE,
            ) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _discard_stale_pending(artifacts: Artifacts) -> None:
    if not artifacts.pending.is_file():
        return
    try:
        pid = int(json.loads(artifacts.pending.read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        pid = -1
    if pid != os.getpid() and not _pid_alive(pid):
        artifacts.pending.unlink(missing_ok=True)
        print("discarded a provisional Claude session left by a dead helper; it is never resumed")


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the run's whole session, including descendants that outlive the leader."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        # macOS answers EPERM for a group whose only member is an unreaped zombie leader.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signum)
        deadline = time.monotonic() + GROUP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None and not _group_alive(process.pid):
                return
            time.sleep(0.05)
    print(f"warning: process group {process.pid} survived SIGKILL", file=sys.stderr)


def _render_prompt(template: Path, *, target: str, extra: str, notes: str) -> str:
    try:
        text = template.read_text(encoding="utf-8")
    except OSError as exc:
        raise ImplementError(f"prompt template not found: {template}", EXIT_USAGE) from exc
    for name, value in (("TARGET", target), ("EXTRA_PROMPT", extra), ("IMPLEMENTER_NOTES", notes)):
        text = text.replace("{{" + name + "}}", value)
    return text + "\n"


def _routing() -> claude_transport.RoutingMap | None:
    try:
        return claude_transport.load_routes()
    except claude_transport.ClaudeTransportError as exc:
        raise ImplementError(str(exc), EXIT_USAGE) from exc


def _codex_fallback(args: argparse.Namespace, artifacts: Artifacts) -> int:
    """No Claude implementer mapping: run today's Codex wrapper (spec scope 1, ADR 0052)."""
    if artifacts.session.exists():
        raise ImplementError(
            f"a Claude session exists for {args.target} but the routing file has no "
            "[implementer]; reset.sh first",
            EXIT_STATE,
        )
    if args.model is not None or args.effort is not None:
        raise ImplementError(
            "--model/--effort apply to the Claude implementer only, which is not mapped",
            EXIT_USAGE,
        )
    print("no Claude implementer mapping; running the Codex implementer", file=sys.stderr)
    forwarded = ["--prompt-file", args.prompt_file]
    if args.action == "resume":
        forwarded += ["--notes", args.notes]
    wrapper = CODEX_WRAPPERS / f"{args.action}.sh"
    argv = ["/bin/bash", str(wrapper), *forwarded, args.target, *args.extra]
    # Like every child of a Claude-coordinated run, the Codex wrapper gets a scrubbed environment.
    environment = claude_transport.child_environment(os.environ, coordinator="claude-code")
    os.execve(argv[0], argv, environment)  # noqa: S606 -- fixed bash and the project wrapper.
    return EXIT_FAILED  # unreachable: execve replaces the process


def _select_model(
    args: argparse.Namespace, routing: claude_transport.RoutingMap
) -> tuple[str, str, str | None]:
    """Model from the target contract's route; an explicit override needs a recorded reason."""
    has_override = args.model is not None or args.effort is not None
    reason = (args.override_reason or "").strip()
    if has_override != bool(reason) or "\n" in reason or len(reason) > 200:
        raise ImplementError(
            "--model/--effort need a one-line --override-reason (and only with them)", EXIT_USAGE
        )
    if os.environ.get("CODEX_MODEL") or os.environ.get("CODEX_EFFORT"):
        raise ImplementError(
            "CODEX_MODEL/CODEX_EFFORT cannot be combined with --coordinator claude-code; "
            "use --model/--effort with --override-reason",
            EXIT_USAGE,
        )
    if routing.implementer is None:
        raise ImplementError("the routing file has no [implementer] entry", EXIT_USAGE)
    route_name = None
    route = routing.implementer
    if args.model is None:
        try:
            route_name, route = claude_transport.resolve_route(Path(args.target), routing)
        except claude_transport.ClaudeTransportError as exc:
            raise ImplementError(str(exc), EXIT_USAGE) from exc
    model = args.model or route.model
    effort = args.effort or route.effort
    if claude_transport.CLAUDE_MODEL.fullmatch(model) is None:
        raise ImplementError(f"the Claude implementer needs a Claude model, got {model}", EXIT_ARGS)
    if effort not in claude_transport.EFFORTS:
        raise ImplementError(f"invalid Claude effort {effort}", EXIT_ARGS)
    return model, effort, route_name


def _timeout() -> float | None:
    raw = os.environ.get("CODEX_TIMEOUT", "0")
    if not raw.isdigit():
        raise ImplementError("CODEX_TIMEOUT must be a whole number of seconds", EXIT_USAGE)
    return None if int(raw) == 0 else float(raw)


def _check_provider_state(args: argparse.Namespace, artifacts: Artifacts) -> Session | None:
    if args.action == "resume":
        if not artifacts.session.is_file():
            raise ImplementError(
                f"no Claude session for {args.target}; start.sh --coordinator claude-code first",
                EXIT_STATE,
            )
        return Session.load(artifacts.session)
    if artifacts.codex_thread.exists():
        raise ImplementError(
            f"a Codex implementation thread exists for {args.target}; resume it without "
            "--coordinator claude-code, or reset.sh first",
            EXIT_STATE,
        )
    if artifacts.session.exists():
        raise ImplementError(
            f"a Claude session exists for {args.target}; use resume.sh, or reset.sh first",
            EXIT_STATE,
        )
    return None


def _run_claude(
    command: list[str], artifacts: Artifacts, *, worktree: Path, deadline: float | None, model: str
) -> int:
    """Run one Claude turn; every started process group is stopped before returning."""
    process: subprocess.Popen[bytes] | None = None
    try:
        with artifacts.prompt.open("rb") as stdin, artifacts.events.open("wb") as event_log:
            with CANCELLATION.deferred():
                process = subprocess.Popen(  # noqa: S603 -- resolved Claude CLI, fixed arguments.
                    command,
                    stdin=stdin,
                    stdout=subprocess.PIPE,
                    cwd=worktree,
                    env=claude_transport.child_environment(os.environ, coordinator="claude-code"),
                    start_new_session=True,
                )
            return claude_transport.follow_stream(
                process, event_log, deadline=deadline, model=model, profile=PROFILE
            )
    finally:
        if process is not None:
            with _shielded():
                _stop_group(process)


def _launch(args: argparse.Namespace, artifacts: Artifacts, state: Path) -> int:
    # Provenance first: a refused flag never reaches either provider.
    try:
        claude_transport.check_runtime_signal("claude-code")
    except claude_transport.ClaudeTransportError as exc:
        raise ImplementError(str(exc), EXIT_USAGE) from exc
    routing = _routing()
    if routing is None or routing.implementer is None:
        return _codex_fallback(args, artifacts)
    previous = _check_provider_state(args, artifacts)
    model, effort, route_name = _select_model(args, routing)
    timeout = _timeout()
    try:
        binary = claude_transport.resolve_binary(None)
        version = claude_transport.preflight(binary)
    except claude_transport.ClaudeTransportError as exc:
        raise ImplementError(str(exc), EXIT_FAILED) from exc
    worktree = Path.cwd().resolve()
    session_id = previous.session_id if previous else str(uuid.uuid4())
    artifacts.prompt.write_text(
        _render_prompt(
            Path(args.prompt_file), target=args.target, extra=" ".join(args.extra), notes=args.notes
        ),
        encoding="utf-8",
    )
    artifacts.settings.write_text(
        json.dumps(claude_transport.build_settings(PROFILE, worktree, state)), encoding="utf-8"
    )
    command = claude_transport.build_command(
        binary,
        model=model,
        effort=effort,
        profile=PROFILE,
        worktree=worktree,
        settings_path=artifacts.settings,
        system_prompt=RUNNER_CONTRACT,
        session_args=("--resume" if previous else "--session-id", session_id),
        protected=(state,),
    )
    previous_handlers = {
        signum: signal.signal(signum, CANCELLATION.handler) for signum in CANCEL_SIGNALS
    }
    try:
        if previous is None:
            # Provisional until the commit below; a crashed helper's entry is discarded later.
            _publish(artifacts.pending, json.dumps({"session_id": session_id, "pid": os.getpid()}))
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            return_code = _run_claude(
                command, artifacts, worktree=worktree, deadline=deadline, model=model
            )
        except claude_transport.StreamTimeout:
            raise ImplementError(
                f"Claude implementer timed out after {timeout:.0f}s (CODEX_TIMEOUT); "
                "inspect git status and git diff before retrying",
                EXIT_FAILED,
            ) from None
        except claude_transport.ClaudeTransportError as exc:
            raise ImplementError(str(exc), EXIT_FAILED) from exc
        stream = claude_transport.parse_stream(artifacts.events, model=model, profile=PROFILE)
        if stream.error_code == "claude_auth_failed":
            raise ImplementError(
                "Claude authentication failed: run `claude auth login`, or provide "
                "CLAUDE_CODE_OAUTH_TOKEN, then retry. Environment failure: do not escalate.",
                EXIT_FAILED,
            )
        if stream.error_code or return_code != 0:
            raise ImplementError(
                f"Claude implementer run failed ({stream.error_code or f'exit {return_code}'}); "
                f"events: {artifacts.events}",
                EXIT_FAILED,
            )
        if stream.session_id != session_id:
            raise ImplementError("Claude reported a different session id", EXIT_FAILED)
        # The single commit point: session id and report become visible together or not at all.
        record = json.dumps({"session_id": session_id, "report": stream.report})
        staged = _stage(artifacts.session, record)
        try:
            CANCELLATION.commit(lambda: os.replace(staged, artifacts.session))
        finally:
            staged.unlink(missing_ok=True)
    finally:
        with _shielded():
            if previous is None:
                artifacts.pending.unlink(missing_ok=True)
            artifacts.settings.unlink(missing_ok=True)
        # After a commit the gate keeps dropping signals until the process exits with success.
        if not CANCELLATION.committed:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    verb = "resumed" if previous else "started"
    via = route_name or f"override: {args.override_reason}"
    print(f"{verb} Claude implementation session for {args.target}")
    print(f"  session id:   {session_id}")
    print(f"  model/effort/route: {model} / {effort} / {via}")
    print(f"  claude code:  {stream.claude_code_version or version}")
    print(f"  session file: {artifacts.session}")
    print("---")
    print(stream.report)
    return 0


def run(args: argparse.Namespace, *, state: Path | None = None) -> int:
    state = state or STATE_DIR
    artifacts = Artifacts.for_target(state, args.target)
    with _locked(artifacts):
        _discard_stale_pending(artifacts)
        if args.action == "reset":
            for path in artifacts.removable():
                if path.exists():
                    path.unlink()
                    print(f"removed {path}")
            return 0
        if args.action == "show":
            if not artifacts.session.is_file():
                raise ImplementError(f"no Claude session on file for {args.target}", EXIT_FAILED)
            session = Session.load(artifacts.session)
            print(f"claude session: {session.session_id}")
            print(session.report, end="")
            return 0
        return _launch(args, artifacts, state)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("action", choices=("start", "resume", "reset", "show"))
    parser.add_argument("--prompt-file", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--override-reason")
    parser.add_argument("target")
    parser.add_argument("extra", nargs="*")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action in {"start", "resume"} and not args.prompt_file:
        print(
            f"usage: {args.action}.sh --coordinator claude-code --prompt-file <tpl> <target>",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        return run(args)
    except ImplementError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except (Cancelled, KeyboardInterrupt):
        print("error: cancelled; the Claude process group was stopped", file=sys.stderr)
        return EXIT_CANCELLED


if __name__ == "__main__":
    raise SystemExit(main())
