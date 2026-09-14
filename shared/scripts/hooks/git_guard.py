#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block state-changing `git` commands that would run
against the primary checkout instead of a linked worktree,
plus a small set of ref/remote mutations that are unsafe regardless of which
directory they run from.

Why this exists: the Claude Code shell's working directory does not reliably
persist across tool calls in this project's sessions (cd outside the repo,
backgrounded commands, and context compaction have all been observed to reset
it back to the session's launch directory, which for TRIP agents is the
primary checkout). Agents relying on an earlier `cd` have landed commits on
the wrong branch and run `git rebase` against a stale local `main`. See
`.claude/skills/nadili-process/SKILL.md` Stage 2 and the
`verify-cwd-before-every-git-and-gate-command` memory note.

This is a heuristic shell-command parser, not a sandbox: it deliberately
yields (allows) on shell constructs it cannot confidently interpret — a
parser that blocked everything ambiguous would just get routed around. The
one carve-out is directory *ambiguity* for a small set of commands whose
blast radius (discarding uncommitted work, rewriting history, force-pushing)
makes "probably fine" an unacceptable guess: those are refused, not allowed,
when the target directory can't be resolved. See `is_high_risk` /
`block_unknown_location`.
"""

import functools
import json
import os
import re
import shlex
import subprocess  # noqa: S404 -- fixed argv, no shell=True, see helpers below
import sys
from typing import Any

# Fallback only: used when git itself is unavailable, so this repo's identity
# can't be resolved at all (see get_nadili_common_dir). Not otherwise trusted:
# every worktree checks out an identical copy of this script, so treating
# this path as *the* repo root would compute the worktree's own path when
# this script happens to run from inside one.
REPO_ROOT_FALLBACK = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Subcommands that rewrite history, discard work, or move HEAD in ways that
# are always unsafe to run against the primary checkout's working tree.
ALWAYS_BLOCKED = {
    "rebase",
    "stash",
    "reset",
    "clean",
    "cherry-pick",
    "revert",
    "filter-branch",
    "update-ref",
    "am",
    "apply",
    "rm",
}

_SWEEP_ADD_TOKENS = {"-A", "--all", "-u", "--update", ":/", ".", "./"}
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHORT_FLAG_RE = re.compile(r"^-[a-zA-Z]+$")
_TAG_VALUE_FLAGS = {"-m", "--message", "-F", "--file"}


def looks_unexpanded(text: str) -> bool:
    return "$" in text or "`" in text


def in_repo(path: str, repo_root: str) -> bool:
    return path == repo_root or path.startswith(repo_root + os.sep)


def resolve(target: str, base: str | None) -> str | None:
    """Resolve `target` against `base`. Returns None (unknown) for a
    relative target when `base` itself is unknown — better to admit we don't
    know than to guess against some unrelated fallback directory."""
    if os.path.isabs(target):
        return os.path.realpath(target)
    if base is None:
        return None
    return os.path.realpath(os.path.join(base, target))


def rev_parse_dir(anchor: str, flag: str) -> str | None:
    """Absolute, resolved path printed by `git rev-parse <flag>` run from
    `anchor` (works for a repo root or any subdirectory within it — git walks
    up), or None if `anchor` isn't inside a git working tree at all (or
    doesn't exist). Git prints some of these relative to `anchor`, so the
    result is normalized here."""
    if not os.path.isdir(anchor):
        return None
    try:
        out = subprocess.run(  # noqa: S603 -- fixed argv, trusted git executable
            ["git", "rev-parse", flag],  # noqa: S607
            cwd=anchor,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        path = out.stdout.strip()
        path_abs = path if os.path.isabs(path) else os.path.normpath(os.path.join(anchor, path))
        return os.path.realpath(path_abs)
    except Exception:
        return None


def git_common_dir(anchor: str) -> str | None:
    """Absolute, resolved path to the git common dir that `anchor` belongs
    to, or None if it isn't inside a git working tree. `--git-common-dir`
    always points at a repo's main `.git`, whether run from its primary
    checkout or from any of its worktrees, so this doubles as an identity
    check: two anchors have the same common dir iff they belong to the same
    repo."""
    return rev_parse_dir(anchor, "--git-common-dir")


def is_git_worktree(anchor: str) -> bool:
    """Whether `anchor` sits in a linked worktree rather than in the primary
    checkout.

    Git is asked instead of matching a path prefix. Worktrees have lived under
    both `.codex/worktrees/` and `.claude/worktrees/` in this repo, and a
    prefix that knows only one of them labels the other "primary" — which
    refuses every ordinary commit made from it while protecting nothing. Git's
    own answer does not depend on where the checkout was placed: a linked
    worktree's git dir lives under `<common dir>/worktrees/`, while the primary
    checkout's git dir *is* the common dir."""
    common = git_common_dir(anchor)
    git_dir = rev_parse_dir(anchor, "--git-dir")
    if common is None or git_dir is None:
        return False
    return git_dir.startswith(os.path.join(common, "worktrees") + os.sep)


@functools.lru_cache(maxsize=1)
def get_nadili_common_dir() -> str | None:
    """This repo's common dir, resolved once per hook process from this
    script's own location. Safe to anchor on __file__ here specifically
    because we only ask git to *identify* the repo (which is shared and
    identical from any worktree), never to compute a *path* from it — that
    distinction is what the worktree-copy bug above was about."""
    return git_common_dir(os.path.dirname(os.path.realpath(__file__)))


@functools.lru_cache(maxsize=1)
def get_nadili_repo_root() -> str:
    common = get_nadili_common_dir()
    return os.path.dirname(common) if common else REPO_ROOT_FALLBACK


def is_nadili_target(effective_dir: str) -> bool:
    """Whether `effective_dir` belongs to *this* repo, not some unrelated
    git checkout the command happens to also touch. Without this, a command
    like `git -C /some/other/repo reset --hard` would be judged against
    Nadili's policy purely because that path isn't under Nadili's worktrees
    prefix — wrongly treating every unrelated repo's root as "primary"."""
    nadili_common = get_nadili_common_dir()
    if nadili_common is None:
        return False
    return git_common_dir(effective_dir) == nadili_common


def split_top_level(command: str) -> list[str]:
    """Split a shell command into simple-command segments on `&&`, `||`,
    `;`, `|`, and newlines — but only outside single/double quotes, so a `;`
    or `|` embedded in a quoted argument (e.g. a commit message) doesn't
    fracture the command mid-token and cause the real command to be silently
    skipped when the fragments fail to shlex-parse."""
    segments: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(command)
    quote: str | None = None
    while i < n:
        c = command[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(command[i + 1])
            i += 2
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "|", "\n"):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segments.append("".join(buf))
    return segments


def strip_leading_assignments(argv: list[str]) -> list[str]:
    """Drop leading `NAME=value` tokens (e.g. `GIT_SEQUENCE_EDITOR=true git
    rebase ...`) so the command word after them is still recognized."""
    i = 0
    while i < len(argv) and _ASSIGNMENT_RE.match(argv[i]):
        i += 1
    return argv[i:]


def parse_git_invocation(args: list[str]) -> tuple[str | None, str | None, list[str]]:
    """Return (explicit_dir, subcommand, rest_args) for a `git ...` argv (args
    excludes the leading 'git' token)."""
    explicit_dir = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-C":
            explicit_dir = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if a.startswith("-C") and len(a) > 2:
            explicit_dir = a[2:]
            i += 1
            continue
        if a == "-c":
            i += 2
            continue
        if a.startswith("--git-dir=") or a.startswith("--work-tree="):
            i += 1
            continue
        if a.startswith("-"):
            i += 1
            continue
        return explicit_dir, a, args[i + 1 :]
    return explicit_dir, None, []


def get_head_branch(path: str) -> str | None:
    # path is derived from the parsed command's own -C/cd targets, not from
    # untrusted external input; argv is fixed (no shell=True).
    try:
        out = subprocess.run(  # noqa: S603 -- fixed argv, trusted git executable
            ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:
        return None


def _has_short_flag(rest: list[str], letter: str) -> bool:
    return any(_SHORT_FLAG_RE.match(tok) and letter in tok[1:] for tok in rest)


def _is_sweep_add(rest: list[str]) -> bool:
    return any(x in _SWEEP_ADD_TOKENS for x in rest)


def _is_forced_push(rest: list[str]) -> bool:
    if any(f == "--force" or f == "-f" or f.startswith("--force-with-lease") for f in rest):
        return True
    return any(a.startswith("+") for a in rest if not a.startswith("-"))


def _is_commit_rewrite_or_sweep(rest: list[str]) -> bool:
    if "--amend" in rest or any(x.startswith(("--fixup", "--squash")) for x in rest):
        return True
    return any(x in ("-a", "--all") for x in rest) or _has_short_flag(rest, "a")


def _is_unsafe_restore(rest: list[str]) -> bool:
    has_staged = "--staged" in rest or "-S" in rest
    has_worktree = "--worktree" in rest or "-W" in rest
    return not (has_staged and not has_worktree)


def _is_branch_delete(rest: list[str]) -> bool:
    return any(x in ("-d", "-D", "--delete") for x in rest if x.startswith("-"))


def _is_new_branch_checkout(rest: list[str]) -> bool:
    """Strict allowlist, not a denylist of known-bad flags — checkout/switch
    have many flags that interact with existing working-tree state in ways
    that aren't "create fresh, nothing lost" (`-f`/`--force`,
    `-m`/`--merge`, `--discard-changes`, `-B`/`-C` force-create, and whatever
    else turns up that we haven't been asked about yet); denylisting them
    one at a time is a losing game. Only the exact documented pattern
    (nadili-process/implement.md's fallback — plain `-b`/`-c`, a branch name, and an
    optional start-point, nothing else) is treated as safe. Anything else —
    any other flag, any extra positional argument — falls through to the
    ordinary checkout/switch block below rather than being waved through."""
    create_flag = False
    positional: list[str] = []
    for tok in rest:
        if tok in ("-b", "-c"):
            if create_flag:
                return False
            create_flag = True
            continue
        if tok.startswith("-"):
            return False
        positional.append(tok)
    return create_flag and 1 <= len(positional) <= 2


def _tag_names(rest: list[str]) -> tuple[list[str], bool]:
    """Return (non-flag tokens, is_list_or_delete), skipping the value each
    arity-1 flag (like -m/--message) consumes so it isn't miscounted as a
    commit-ish (`git tag -a v1 -m release` must not treat 'release' as one)."""
    names: list[str] = []
    is_list_or_delete = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in _TAG_VALUE_FLAGS:
            i += 2
            continue
        if tok.startswith("--message=") or tok.startswith("--file="):
            i += 1
            continue
        if tok in ("-d", "--delete", "-l", "--list"):
            is_list_or_delete = True
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        names.append(tok)
        i += 1
    return names, is_list_or_delete


def is_high_risk(subcommand: str | None, rest: list[str]) -> bool:
    """Whether this git subcommand is dangerous enough that an unresolvable
    target directory must be refused rather than allowed through."""
    if subcommand in ALWAYS_BLOCKED:
        return True
    if subcommand == "restore":
        return _is_unsafe_restore(rest)
    if subcommand == "push":
        return _is_forced_push(rest)
    if subcommand == "commit":
        return _is_commit_rewrite_or_sweep(rest)
    if subcommand == "branch":
        return _is_branch_delete(rest)
    return False


def block_unknown_location(segment: str, reason: str) -> None:
    msg = (
        "git_guard: blocked a command whose target directory could not be determined "
        '(it references an unexpanded shell variable, e.g. `git -C "$WT"` or '
        '`cd "$WT"`, or a `cd` into a directory that is not a git checkout).\n\n'
        f"  command: {segment.strip()}\n"
        f"  reason:  {reason}\n\n"
        "This command is high-risk enough (discards or rewrites state, or force-pushes) "
        "that guessing it's safe isn't acceptable. Resolve the variable to an absolute "
        "path first, e.g. `WT=$(cd <your worktree> && pwd)`, or use "
        "`git -C /absolute/path/to/worktree ...` directly."
    )
    print(msg, file=sys.stderr)
    sys.exit(2)


def block(
    segment: str, effective_dir: str, reason: str, repo_root: str, in_worktree: bool = False
) -> None:
    rel = os.path.relpath(effective_dir, repo_root)
    if in_worktree:
        msg = (
            "git_guard: blocked a command that mutates shared state (branches or the "
            f"remote) even though it runs from a worktree ({rel}).\n\n"
            f"  command: {segment.strip()}\n"
            f"  reason:  {reason}\n\n"
            "Worktrees share one ref namespace and one remote, so this isn't a cwd-drift "
            "issue — it's a cross-item action. If this really is your own branch or your "
            "own push, double-check the branch name and retry; if it names something you "
            "didn't create, it targets a different item's work."
        )
    else:
        msg = (
            "git_guard: blocked a command that would run against the PRIMARY checkout "
            f"({'repo root' if rel == '.' else rel}), not a worktree.\n\n"
            f"  command: {segment.strip()}\n"
            f"  reason:  {reason}\n\n"
            "The primary checkout is shared ground other agents are using right now "
            "(nadili-process/SKILL.md Stage 2). This is very likely the cwd-drift bug, not "
            "an intentional primary-checkout operation: the Claude Code shell's cwd does "
            "not reliably persist across tool calls in this project.\n\n"
            "Fix: use `git -C /absolute/path/to/your/worktree ...` "
            "for every git command, or `cd` back into your worktree and re-verify with "
            "`git rev-parse --show-toplevel` before retrying. If this really is an "
            "intentional primary-checkout operation (e.g. Step 14 worktree removal), "
            "it isn't one of the operations this guard allows there — do it manually "
            "or extend the allowlist in scripts/hooks/git_guard.py."
        )
    print(msg, file=sys.stderr)
    sys.exit(2)


def check_git_command(
    segment: str,
    effective_dir: str,
    subcommand: str | None,
    rest: list[str],
    repo_root: str,
    in_worktree: bool,
) -> None:
    if subcommand is None:
        return

    # Branch deletion and force-push mutate state that is effectively
    # repo-global — worktrees share one ref namespace and one remote — so
    # these two apply no matter which directory the command runs from.
    if subcommand == "branch":
        if _is_branch_delete(rest):
            names = [x for x in rest if not x.startswith("-")]
            if not names:
                block(
                    segment,
                    effective_dir,
                    "branch delete with no branch name given",
                    repo_root,
                    in_worktree,
                )
                return
            # work/<ID> (worktree items) and feat/…, fix/… (the primary-checkout
            # fallback for docs-only/hotfix work, nadili-process/implement.md) are this
            # project's two sanctioned branch-naming conventions. Each name is
            # checked individually — a mixed list like
            # `branch -D work/NAD-999 feat/done` must not let one safe-looking
            # name wave through another that isn't.
            expected_own = None
            if in_worktree:
                expected_own = "work/" + os.path.basename(effective_dir.rstrip(os.sep)).upper()
            for name in names:
                if name.startswith("work/"):
                    if in_worktree and name.lower() != expected_own.lower():  # type: ignore[union-attr]
                        block(
                            segment,
                            effective_dir,
                            f"deleting a work/<ID> branch other than this worktree's own "
                            f"({expected_own}): {name}",
                            repo_root,
                            in_worktree,
                        )
                        return
                    continue
                if name.startswith(("feat/", "fix/")):
                    if in_worktree:
                        block(
                            segment,
                            effective_dir,
                            f"feat/fix branches only ever exist in the primary checkout — "
                            f"deleting '{name}' from a worktree targets someone else's branch",
                            repo_root,
                            in_worktree,
                        )
                        return
                    continue
                block(
                    segment,
                    effective_dir,
                    f"deleting a branch that isn't a completed work/<ID>, feat/, or fix/ "
                    f"branch: {name}",
                    repo_root,
                    in_worktree,
                )
                return
        return
    if subcommand == "push":
        if _is_forced_push(rest):
            block(
                segment,
                effective_dir,
                "force-push can rewrite shared remote history",
                repo_root,
                in_worktree,
            )
        return

    if in_worktree:
        return  # everything below only matters for the primary checkout

    if subcommand in ALWAYS_BLOCKED:
        block(
            segment,
            effective_dir,
            f"'git {subcommand}' rewrites or discards history/state",
            repo_root,
        )
        return
    if subcommand == "restore":
        if _is_unsafe_restore(rest):
            block(
                segment,
                effective_dir,
                "restore touches the working tree (missing --staged, or --worktree/-W "
                "given alongside it) and can discard uncommitted changes",
                repo_root,
            )
        return
    if subcommand in ("checkout", "switch"):
        if rest == ["main"]:
            return
        if _is_new_branch_checkout(rest):
            return
        block(
            segment,
            effective_dir,
            f"'git {subcommand} {' '.join(rest)}' switches to an existing ref — it could be "
            "another agent's in-flight branch",
            repo_root,
        )
        return
    if subcommand == "merge":
        if "--ff-only" in rest:
            return
        block(
            segment,
            effective_dir,
            "merge without --ff-only can create a merge commit or conflict state",
            repo_root,
        )
        return
    if subcommand == "pull":
        if "--ff-only" in rest:
            return
        block(
            segment,
            effective_dir,
            "pull without --ff-only can implicit-merge, or (with pull.rebase set) rebase, "
            "against shared primary main",
            repo_root,
        )
        return
    if subcommand == "add":
        if _is_sweep_add(rest):
            block(
                segment,
                effective_dir,
                "sweeps up neighbouring agents' untracked or modified files",
                repo_root,
            )
        return
    if subcommand == "commit":
        if "--amend" in rest or any(x.startswith(("--fixup", "--squash")) for x in rest):
            block(
                segment,
                effective_dir,
                "amend/fixup/squash rewrites the tip commit of shared primary main",
                repo_root,
            )
            return
        if any(x in ("-a", "--all") for x in rest) or _has_short_flag(rest, "a"):
            block(
                segment,
                effective_dir,
                "commit -a/--all sweeps neighbouring agents' tracked-file changes into the commit",
                repo_root,
            )
            return
        head_branch = get_head_branch(effective_dir)
        if head_branch != "main":
            block(
                segment,
                effective_dir,
                f"HEAD is '{head_branch}', not 'main' — SKILL.md Stage 2 requires asserting main "
                "before any commit in the primary checkout; this would land on someone "
                "else's branch",
                repo_root,
            )
        return
    if subcommand == "tag":
        if not rest:
            return
        names, is_list_or_delete = _tag_names(rest)
        if is_list_or_delete or len(names) >= 2:
            return
        block(
            segment,
            effective_dir,
            "creating a tag without an explicit commit-ish tags primary's HEAD, not the "
            "release commit — this exact failure has happened before (NAD-58, v0.1.19)",
            repo_root,
        )
        return
    # fetch, worktree, status, log, diff, show, rev-parse, ls-remote, remote,
    # symbolic-ref, describe, config, etc. — read-only or documented as
    # safe/expected in the primary checkout.


def main() -> int:
    try:
        payload: dict[str, Any] = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command.strip() or "git" not in command:
        return 0  # cheap pre-filter: skip parsing entirely for non-git commands

    session_cwd = payload.get("cwd") or REPO_ROOT_FALLBACK
    tracked_dir: str | None = session_cwd

    for raw_segment in split_top_level(command):
        segment = raw_segment.strip()
        if not segment:
            continue
        try:
            argv = shlex.split(segment)
        except ValueError:
            continue
        argv = strip_leading_assignments(argv)
        if not argv:
            continue

        head = argv[0]
        if head == "cd":
            target = argv[1] if len(argv) > 1 else os.path.expanduser("~")
            if looks_unexpanded(target):
                tracked_dir = None
            else:
                candidate = resolve(target, tracked_dir)
                # git_common_dir (not a bare ".git" existence check) so a cd
                # into a *subdirectory* of a worktree — not just its root —
                # is still correctly recognized as a real git checkout.
                tracked_dir = candidate if candidate and git_common_dir(candidate) else None
            continue

        if head == "git" or head.endswith("/git"):
            explicit_dir, subcommand, rest = parse_git_invocation(argv[1:])
            if explicit_dir is not None and looks_unexpanded(explicit_dir):
                effective_dir = None
            elif explicit_dir:
                effective_dir = resolve(explicit_dir, tracked_dir)
            else:
                effective_dir = tracked_dir

            if effective_dir is None:
                if is_high_risk(subcommand, rest):
                    block_unknown_location(
                        segment, f"'git {subcommand}' targets an unresolved/unknown directory"
                    )
                continue

            if not is_nadili_target(effective_dir):
                continue  # a different repo entirely — not this guard's business

            repo_root = get_nadili_repo_root()
            if not in_repo(effective_dir, repo_root):
                continue
            in_worktree = is_git_worktree(effective_dir)
            check_git_command(segment, effective_dir, subcommand, rest, repo_root, in_worktree)

    return 0


if __name__ == "__main__":
    sys.exit(main())
