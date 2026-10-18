---
name: codex-role
description: In Claude Code, dispatch a Nadili .codex/agents role (backend/frontend QA tester, plan reviewer, architect, code explorer, executor) as a fresh one-shot CLI run through shared/scripts/codex_role_dispatch.py, on Codex or on Claude per shared/claude/role-models.toml, never as an in-session Claude subagent.
---

# Role dispatch for Claude Code

Codex opens the roles in `adapters/gstack/roles/*.toml` as native agent threads; Claude Code cannot.
In Claude Code, **every** fresh-context role that a Nadili stage contract dispatches goes through
`shared/scripts/codex_role_dispatch.py`, always with `--coordinator claude-code`. The runner starts one
separate, sandboxed CLI process per run and applies the role file: developer instructions,
sandbox class, and (on Codex) model, effort and tool surface. Codex sessions keep their native
roles; nothing here changes `adapters/gstack/` or `shared/`.

## Transport

`shared/claude/role-models.toml` (spec D-001, ADR 0052) decides the transport per role:

| Role | Transport | Model / effort |
|---|---|---|
| `executor` | Claude | from the item plan's `**Workflow route:**`: Small/Standard → `claude-haiku-5-5`, High-risk → `claude-sonnet-5-5`; effort high |
| `code_explorer`, `focused_backend_qa`, `focused_frontend_qa` | Claude | `claude-haiku-5-5`, high |
| `architect` | Claude | `claude-opus-5-5`, medium, read-only |
| `backend_qa`, `frontend_qa`, all plan reviewers, `claim_researcher` | Codex | role TOML, unchanged (D-002) |

- A `claude -p` run started by this runner is a fresh-context role run in its own process and
  sandbox. It is **not the forbidden in-session Claude `Agent` subagent** (D-004). Never use the
  `Agent` tool for these roles.
- The Claude `executor` needs `--item-id`. A missing or invalid route line in
  `docs/work/<ID>/plan.md` (or `hotfix.md`) is refused with exit 64 — add the line; there is no
  default model.
- `--transport codex` runs a Claude-mapped role on its Codex TOML instead. Use it only when the
  owner chooses that fallback, for example while Claude authentication is broken.
- Prerequisites for the Claude transport: a current CLI (`claude update`; the runner refuses
  versions below its verified minimum) and a standalone login (`claude auth login`, or
  `CLAUDE_CODE_OAUTH_TOKEN`). An authentication failure exits 1 with `claude_auth_failed`: report
  it to the owner; it is an environment failure, never a reason to escalate or switch silently.

## Stage to role

Routing, risk and model choice stay owned by `adapters/gstack/orchestrator.md` (§ QA dispatch contract)
and `adapters/gstack/proportional-workflows.md`. This table only names the role.

| Stage | Role (`--role`) |
|---|---|
| 1 Plan review, Small product lens | `small_product_reviewer` |
| 1 Plan review, Standard | `standard_plan_reviewer` |
| 1 Plan review, High-risk or legacy/in-flight | `reviewer` |
| 2 Architecture decision | `architect` |
| Narrow code question | `code_explorer` |
| 3 Bounded implementation batch | `executor` |
| 4 Code review | not this skill: `shared/scripts/codex_code_review.py` |
| 5 Backend QA | `focused_backend_qa` (simple oracle) or `backend_qa` (named risk) |
| 6 Frontend QA | `focused_frontend_qa` (simple oracle) or `frontend_qa` (named risk) |

TRIP items keep their adapter skills (`codex-plan-review`, `codex-implement`,
`codex-code-review`). Use this skill for their Stage 5/6 testers.

## Run it

Write the handoff brief to a file **outside** the item worktree (scratchpad), then run in the
background. A role run takes minutes to tens of minutes. Wait for the single completion
notification; do not poll. At most two role runs may be open at once.

```bash
cd <absolute-item-worktree> && venv/bin/python shared/scripts/codex_role_dispatch.py \
  --coordinator claude-code --role backend_qa --worktree <absolute-item-worktree> \
  --brief <brief.md> --item-id NAD-<ID> --action-id backend-qa-1 --stage backend-qa
```

- `--item-id/--action-id/--stage` reserve, start and finish the attempt in the item budget
  ledger. Exit code 3 is a ledger refusal: an exhausted non-review ceiling needs the owner's
  `amend`; never rename the action to evade it. Omit the three flags only for item-less work
  such as discussion.
- Every run with a new or changed brief is a fresh dispatch with a **new** `--action-id`: a
  retest after a fix is `backend-qa-retest-1`; a corrected brief after a BLOCKED report is
  `backend-qa-2`. Each counts as another agent thread. Use `--attempt 2` only to rerun the
  **unchanged** brief after a crashed, timed-out or infrastructure-failed run with no report.
- Model escalation needs a named reason and stays on the role's transport:
  `--model claude-sonnet-5-5 --override-reason "<named risk>"` for a Claude role (for example
  an executor that repeated a finding after a correction), `--model gpt-6.1-sol --effort high
  --override-reason "<named risk>"` for a Codex role. The reason is recorded in the ledger and
  the run summary. Never escalate for an environment failure.
- `--timeout` defaults to 3600 s. On timeout, Ctrl-C, SIGTERM or SIGHUP the runner stops every
  process group it started (role process and browser server), records the attempt as
  interrupted and exits 1 (timeout) or 130 (cancelled).
- The JSON summary on stdout names `transport`, `model`, `final_message` (the role's report)
  and, for Claude runs, `route` and `session_id`. Read the report and verify that a tester wrote
  its section in `docs/work/<ID>/verification.md`. The coordinator never edits a tester's
  verdict. FAIL goes back to the implementer.

## Sandboxes

- Read-only roles (plan reviewers, `architect`, `code_explorer`) cannot write. On Claude they
  get only Read, Grep and Glob, so put any commit ids or diff paths they need into the brief.
- `executor` writes only inside the item worktree and has no network.
- QA roles (`*_qa`) write inside the worktree and get network so they reach local services and
  the Docker daemon. The sandbox cannot limit that to loopback; the role contract keeps them off
  external hosts. Heavy runtimes still go through `shared/scripts/gate_executor.py`.
- Frontend QA roles, on either transport, use a headless Chromium the runner hosts outside the
  sandbox, because Chromium cannot start inside it. The runner resolves Playwright from the
  worktree's `node_modules`, so a `.claude/worktrees` checkout needs `CI=true pnpm install`
  first, or pass `--playwright-module`.

## Handoff brief

Bounded paths and deltas, never copied documents or this conversation. A role reports BLOCKED
when a required field is missing, so fill every applicable line:

```markdown
# Handoff: <role> — NAD-<ID> <brief>
- Item / worktree: NAD-<ID> / <absolute path>
- Spec / plan (or hotfix contract): <paths>; approved decisions: <ids or "none">
- Draft digests (plan reviewers): <SHA-256 of the spec and plan under review>
- Frozen candidate: <commit sha> (+ "clean tree" or the staged diff identity)
- Task: <exact scenarios or question>; owned/assigned paths: <paths>
- Runtime: <URLs, ports, how it was started or how to start it disposably>
- Test identity: <account/fixture or "anonymous">; viewport scope: <frontend only>
- Acceptance oracle: <expected observations>; failure oracle: <what is FAIL>
- Evidence path: <e.g. docs/work/<ID>/verification.md § Backend QA>
- Named risks / routing reason: <or "none: simple oracle">
- Remaining budget: <attempts/retests left>
```
