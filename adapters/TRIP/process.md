# Nadili TRIP adapter: detailed legacy procedure

`shared/process/SKILL.md` is the Nadili delivery authority. `AGENTS.md` selects the active adapter for new
items; `adapters/TRIP/adapter.md` maps this procedure to TRIP. This detailed legacy reference remains in place
for current TRIP items. Its TRIP-specific sequencing applies only to TRIP items; the main
process wins where wording diverges. Do not load this file for a gstack item.

**Current readiness override:** A frozen spec alone no longer moves a new item to
`Ready for Development`. `nadili-plan` waits for a reviewed plan, architecture verdict and
owner approval; it stops before implementation. The older status and speedrun paragraphs
below are historical TRIP mechanics, not Nadili routing.

**Why this file exists.** The TRIP skills are vendor-maintained and `TRIP-upgrade` rewrites them
from the upstream template, preserving only a fixed list of named customizations
(`PROJECT_NAME`, `TECHNICAL_CONSIDERATIONS`, `GUIDANCE_SECTIONS`, `DOC_IMPACT_CANDIDATES`,
`PLAN_PREREQUISITES`, lint/typecheck/test commands, `VERSION_FILE`, `WEEK_ANCHOR`,
`REVIEW_CHECKLIST`, `CR_TEMPLATE`, `TEST_STRUCTURE`, `TESTING_PRIORITIES`). Anything else written
into a TRIP `SKILL.md` is **lost on upgrade**.

The project-owned process lives outside the vendor TRIP files, which `TRIP-upgrade` rewrites.
This reference preserves the existing TRIP-specific detail:

| File | Holds |
|---|---|
| `adapters/trip-process.md` (this file) | legacy TRIP detail — worker routing, Linear, worktrees, review, plans, delivery and versions |
| `adapters/TRIP/plan.md` | TRIP planning procedure |
| `adapters/TRIP/implement.md` | TRIP implementation procedure |
| `shared/process/integrate.md` | TRIP integration procedure |
| `adapters/TRIP/shims/` | canonical bodies of the vendor TRIP shims |

`TRIP-1-plan`, `TRIP-2-implement` and `TRIP-3-release` are **shims with zero project content** —
an upgrade can replace them but cannot destroy anything, and
`scripts/check_trip_shims.py` (in both pre-commit gates) fails while a shim is missing.
`--write` restores them.

**Authority order:** `AGENTS.md` for architecture, security and adapter selection;
`SKILL.md` for delivery invariants; this file and its phase procedures for the active TRIP
adapter; vendor TRIP bodies last. A vendor update cannot change a Nadili invariant.

After running `/TRIP-upgrade`, work through `docs/6-memo/trip-upgrade-restore.md` before using
the workflow again.

---

## Quality gate tiers

- The git commit hook runs `bash shared/scripts/pre-commit-fast.sh`: the fast commit sanity gate and the
  installed hook remains enabled; `shared/process/gates.md` owns review checks and evidence reuse.
- A stage containing only `docs/**` and `*.md` takes the reduced docs lane in
  `shared/scripts/pre-commit-fast.sh`.
- The installed hook is verified and self-repaired by `shared/scripts/check_hook_install.py` against
  `shared/scripts/hooks/pre-commit`.
- `bash shared/scripts/pre-commit.sh` remains the full local gate.
- `bash scripts/pre-release.sh` is the explicit, unreduced candidate qualification suite. Its
  heavy sequence holds the one machine-wide OS-backed lease through all children and cleanup;
  nested leaves reuse verified inherited ownership. TRIP-2 uses affected integration checks and
  `release-candidate.sh qualify` runs the full suite only on the exact approved candidate.

`shared/scripts/integration-check.sh` owns the complete merge-base-to-candidate, staged, or dirty path
union and prints capabilities plus reasons. A `--base/--head` range uses the bounded affected
union, including PostgreSQL for backend changes; deferred Admin E2E, runtime liveness, public visual and portability work remains
visible in the reason lines for candidate qualification. The deliberate local `--dirty` and
`--staged` scopes retain the full control-plane expansion; `--dev` uses the dirty scope with its
affected dirty-scope runner profile. Unknown or empty scopes still fail closed to the applicable fallback. It
is not a release gate and cannot authorize production.

### Gate coverage: one capability, one runner

- A wrapper owns its children. Never run both on the same unchanged tree:
  - `pre-release.sh` owns `pre-commit.sh`, PostgreSQL integration, Admin E2E, runtime liveness, and
    public standalone visual validation.
  - `portability-smoke.sh` owns `smoke-api.sh` under the portability profile plus restore,
    rollback, rotation, and image checks.
- A `--base/--head` affected check defers release-only browser/visual/portability capabilities
  to qualification and prints their reasons; selected PostgreSQL checks remain fresh. The deliberate local `--dirty` and
  `--staged` scopes retain the full control-plane expansion.
- Before review, run affected checks under `shared/process/gates.md`. Do not add a broad fast/full run
  solely for review entry; candidate qualification owns the full release gate.
- Run standalone `smoke-api.sh` only when a required real HTTP path is not already exercised by
  portability, or when the default smoke profile has unique behavior that portability plus
  pre-release runtime liveness do not cover. Shared Compose/env edits alone are not sufficient.
- Plans name required capabilities and the minimal covering runners. They must not list a wrapper
  beside its child commands.

### Release activation boundary

`scripts/release-candidate.sh` freezes one source SHA, records a unique attempt through
`prepared -> qualifying -> qualified -> published` (or failed/abandoned/superseded), and holds the
publication lock from reservation through candidate publication. Candidate preparation,
qualification, and publication run locally without a host receipt. The installed host's fixed
read-only admission preflight retains an activation receipt, and forward deployment consumes it.
No environment flag enables or bypasses either boundary.

---

## Worker model routing

Claude and Codex managers use the same `.claude/skills/codex-*` CLI scripts for implementation,
plan review, and code review. A manager MUST NOT substitute a native runtime subagent: native
children may inherit the manager's model and bypass the deliberate cost/quality split.

`.claude/skills/codex-plan-review/scripts/_common.sh` is the single routing authority:

- implementation: `gpt-6-luna`, `high`, `fast` service tier;
- plan and code review: `gpt-6-sol`, `high`, standard routing.

In the private Nadili repository, each codex skill has its own wrappers named `start.sh`,
`resume.sh`, `reset.sh` and `show.sh` in that skill's scripts subdirectory. These wrappers pin the
right `STATE_DIR` and delegate to the shared `codex-plan-review` implementation. They are excluded
from this portfolio snapshot. **Call a skill's own
wrapper, never the shared script with an `export STATE_DIR`** — a forgotten export silently drives
the wrong thread with the wrong model and sandbox.

Managers call the scripts, never raw `codex exec`. The scripts own sandbox mode, persistent CLI
thread state, timeout handling, and output capture. Per-batch implementation effort escalation is
allowed only under the criteria in `.claude/skills/codex-implement/SKILL.md`; it does not change
the implementation model.

Inside a Codex manager task, launch these authenticated networked CLI scripts through the current
terminal permission-escalation mechanism and retain any yielded terminal session until completion.
A sandbox or network denial is not a reason to fall back to a native child; retry the exact script
through escalation or report the blocker.

### Standing Codex-delegation authorization

**Delegating to Codex is pre-approved. Never ask the owner for it — in any phase, at any stage,
in any runtime.** Granted by the repository owner alongside §1.1 and §6; it does not expire with
a session.

It covers, explicitly and permanently:

- **transmitting** the plan or review target, the repository code and diffs Codex needs, and gate
  output, to the external Codex CLI service — every `start.sh` / `resume.sh` call of
  `codex-plan-review`, `codex-code-review`, `codex-implement` and `codex-ask`;
- **`codex-implement` writing** files in the working tree of the item's own worktree, in its
  `workspace-write` sandbox;
- **repeating** all of the above for every batch, every resume, and every phase of the same item.

One grant, not one per call. A prompt asked at TRIP-1 review, again at TRIP-2 implementation and
again at each batch is the same question answered once, already, here.

A runtime approval dialog — the Codex CLI's own sandbox/network escalation, a harness permission
prompt — is a **mechanism to satisfy**, never a decision to hand to the owner: escalate through it
and continue. If escalation is genuinely refused by the runtime, that is a blocker to report in
one line, not a question to ask.

The scripts are safe to run autonomously by construction: reviews and `codex-ask` run
`--sandbox read-only`; `codex-implement` runs `workspace-write` with no network and makes no
commits; nothing Codex returns can deliver, tag, or push anything.

**What still requires an ask** (and is not permitted here anyway): sending secrets, `.env`
contents, credentials or files from outside the repository; letting a Codex worker write outside
the item's own worktree or into the primary checkout.

---

## 1. Linear ticket lifecycle

Every TRIP plan gets a Linear issue, and its status tracks the live phase of the work. With
several agents delivering in parallel, Linear is the only place the owner can see what is
actually in flight without reading four terminals.

### 1.1 Standing authorization

`AGENTS.md` grants every agent, in every runtime, standing authorization to move a work item's
issue between statuses and post bounded status comments. Creating the issue for a TRIP plan is
part of that flow. **Do not ask for permission for any step in this section** — not to create the
issue, not to move it, not to comment. Ask only if a step would delete an issue, edit an issue's
pinned specification, or push logs/diffs/credentials into the tracker, none of which are allowed
here.

### 1.2 Linear is non-authoritative

Binding rules:

- A Linear write **never** affects routing, gating, or workflow state. The plan file, the git
  branch, and the testing gate are the truth.
- A **failed Linear write is not a failure of the work.** Report it in one line and continue.
  Never retry in a loop, never block a release on it, never ask the owner to fix it mid-flow.
- Writes must be **idempotent**. Before creating or transitioning, read the current state first;
  a re-run of the same phase must not create a second issue or post a duplicate comment.

### 1.3 Creating the issue (TRIP-1 Step 2, **before** the plan file is written)

The plan's path contains the issue id (§4), so the issue comes first. In TRIP-1 Step 2: draft the
plan content, create the issue, then write the file to the id-derived path.

**If the work started from an existing Linear issue, skip creation** — use that id.

For chat-registered work, `docs/CHAT-TO-LINEAR-INTAKE.md` already created the issue in
`Specifying` with a committed specification whose filename contains the issue ID. Read that
specification from the repository; if a legacy issue has `Spec: <path> @ <sha>`, read that
revision. Treat its scope as settled — **skip TRIP-1 discovery entirely** — and write the plan
into the same item folder. Never
open a second issue, never move the ready issue backwards to `Specifying`, and never edit the
committed spec as part of planning; if it is wrong, that is a `Blocked` stop, not a silent rewrite.

Otherwise create it:

- **Team**: `NADILI`.
- **Title**: the feature name from the drafted `# <Feature Name> Implementation Plan` heading.
- **Description**: the drafted **Overview**, then `Plan: docs/work/<ID>/plan.md`, then
  `Release type: patch|minor|major` (never a version number — §7). Nothing else: no diffs, no
  logs, no full plan body. The plan file in git is the specification; Linear is a status surface.
- **Status**: `Specifying` while the executable specification is not yet frozen.
- **Project**: `NADILI MVP` unless the plan clearly belongs elsewhere.

Then write the plan to `docs/work/<ID>/plan.md` with the id echoed as the first line after the
title, for readability:

```markdown
**Linear**: NAD-123
```

**Idempotency**: the path *is* the key. Before creating anything, check whether
`docs/work/<ID>/plan.md` already exists — if it does, this is a re-run: reuse that issue, never
create a second, and edit the existing plan in place.

**If the Linear write fails**, do not block planning. Write the plan to
`docs/work/UNTRACKED-<short-slug>/plan.md`, say so in one line, and continue. When an issue is
created later, move the folder to `docs/work/<ID>/` and update the `**Linear**:` line.

### 1.4 Status mapping

The readiness invariant for new items follows `shared/process/SKILL.md` Stage 2: a committed specification,
reviewed plan, architecture decision, owner approval, and pushed item branch. `Specifying`
covers intake and planning before that handoff. Existing ready issues remain ready and must
never be moved backwards.

Move the issue at each real transition. One transition, no comment, unless the table says otherwise.

| When | Status | Comment? |
|---|---|---|
| Work has no frozen executable spec yet | `Specifying` | no |
| Frozen executable spec becomes available during intake | keep `Specifying` until plan approval | no |
| Plan created for an already-ready legacy item | keep `Ready for Development` | no |
| Owner approves the reviewed plan and the item branch is pushed | `Ready for Development` | no |
| Implementation started (TRIP-2 Step 0, worktree created) | `In Progress` | no |
| Codex code review loop started (TRIP-2) | `Code Review` | no |
| Codex returned `APPROVED`, final delivery commit passed the affected integration check | `Testing` | no |
| Pushed to `origin/main` (TRIP-3 Step 13), item is **owner-verifiable** (§1.4.1) | `UAT` | **yes** — delivery comment |
| Pushed to `origin/main` (TRIP-3 Step 13), item is **agent-verified** (§1.4.1) | `Done` | **yes** — delivery comment |
| Stopped on an owner decision or blocker | `Blocked` | **yes** — one bounded comment naming the decision needed |
| Owner abandoned the item | `Canceled` | **yes** — one line on why |

**No silent reversions, anywhere in this table.** A status transition encodes a precondition —
`In Progress` means "a worktree with the plan exists," `Code Review` means "Codex is actually
reviewing." If that precondition later turns out false (a step failed, a self-heal didn't work),
the only legal move is forward into `Blocked` with a one-line comment naming the exact blocker —
**never backward to a prior status with no comment.** A reverted status with no trace is
indistinguishable from data loss to an owner tracking several items at once, and it has already
happened on this repo: an item was set `In Progress` before its worktree existed, failed almost
immediately, and silently reverted to `Ready for Development` with zero trace. Resuming from
`Blocked` moves forward from wherever the fix lands — never backward either.

**Verify the precondition before writing the status, not after.** For `In Progress` this is one
command with a binary answer — do not assess it by eye:

```bash
venv/bin/python shared/scripts/check_item_ready.py <ID>
```

Exit 0 means the worktree is registered, you are standing inside it, and the plan is present —
set the status. Exit 1 names exactly what is wrong; fix it and re-run, or stop and go to
`Blocked`. Linear has no API token here, so the transition itself cannot be mechanised, but the
judgment behind it is: the script leaves nothing to assess and nothing to skip.

#### 1.4.1 Terminal status: `UAT` or `Done`

An item's terminal status is **decided by one question, and only this question**:

> **Does a human looking at the running product change the verdict?**

`UAT` is the owner's manual-verification queue — a queue of work whose acceptance genuinely
depends on human eyes or human taste. Everything else must not enter it. An item parked in `UAT`
that no human can meaningfully verify is not "safe"; it is noise that corrupts the status of its
parent epic and project and makes the real queue unreadable. Routing such an item to `UAT` is a
process violation, exactly like routing an unverified UI change to `Done`.

**Route to `UAT` (owner-verifiable)** — the change alters something a person perceives while
using the product, *and* the owner's judgment (taste, wording, layout, feel, "is this the
behavior I wanted") is what settles acceptance:

- Admin Web or public-web UI: layout, styling, component appearance, empty/loading/error states.
- UX or interaction flow: navigation, step order, form behavior, what happens after an action.
- User-facing copy, labels, microcopy, translations.
- A new or reshaped user-facing feature or screen, however small.
- Product behavior the owner asked for by feel rather than by specification ("it should refresh
  right after applying", "this list feels wrong").
- Anything where the plan itself records an open question only the owner can answer by looking.

**Route to `Done` (agent-verified)** — the change is settled by the quality gates, the code
review and the release evidence, with nothing left for a human to look at:

- Backend logic with no visual surface: services, repositories, pipeline stages, filters,
  scoring, scheduling, migrations.
- Refactors, renames, deletions, dependency bumps, type or lint cleanups.
- Tests, fixtures, test infrastructure, gate scripts, hooks, harness and TRIP process changes.
- Infrastructure, Compose, deployment, CI, secrets handling, runbooks.
- Documentation-only changes.
- Performance, cost, logging, metrics and observability work.
- SEO and metadata work — titles, descriptions, canonical tags, `robots.txt`, sitemaps,
  structured data. Verification is machine inspection of the emitted markup, which the gate and
  the code review already do; an owner staring at a page cannot confirm it.
- API contract changes with no UI consumer surface yet.

**Mixed items follow their user-visible part**: if a change ships any owner-verifiable behavior at
all, it goes to `UAT`, even if 90% of the diff is backend.

**Ties go to `UAT`, and a tie is narrow.** A tie means you can name the specific thing the owner
would look at and the specific judgment they would make. "I am not sure" is not a tie — decide it
with the list above. Never route to `UAT` to hedge, to share responsibility, or because the item
felt large.

**Rules that hold either way:**

- The classification is recorded in the plan (`Verification:` line, TRIP-1) and applied at
  TRIP-3 Step 13. An agent never re-decides it to avoid a `Done`, and never re-decides it to
  avoid a `UAT`.
- Both routes still post the one delivery comment (§1.5). `Done` does not mean silent.
- An agent may set `Done` **only** on the item it just shipped, in the same release, under this
  rule. It never sets `Done` on any other item, never "tidies up" an existing `UAT` item, and
  never closes someone else's work — those remain the owner's alone.
- An item sitting in `UAT` is not stalled and needs no chasing. It is waiting on a human, by
  design.
- `Done` reached this way is not a claim of perfection; it is a claim that the gates, the code
  review and the release evidence were the whole of the available verification. If the owner
  reopens it, that is normal.

Leaving `Blocked`: move back to the status the work was in when it stopped, and do not post a
second comment.

### 1.5 Comment policy

At most **one comment per stop**, and only for the three rows marked above. Bounded means:

- **Delivery comment**: this is the owner's verification brief, not a receipt. It is the only
  thing standing between "shipped" and "forgotten", so lead with what to check:

  ```text
  Integrated on main — <one-line commit message> (<short sha>)

  To verify:
  - <user-visible behavior 1, and where to see it: which screen, endpoint, or command>
  - <user-visible behavior 2>
  - <anything deliberately NOT covered by automated tests, or a P3 deferred on purpose>

  CR: docs/3-code-review/CR_NAD-<ID>.md
  Changelog: docs/2-changelog/NAD-<ID>.md
  ```

  Write the verify bullets from the plan's outcome, in the owner's terms — "the Answers list
  refreshes right after applying a catalog plan", not "added an `onApplied` callback". Two to four
  bullets. Keep the whole comment under ~10 lines.

  For an **agent-verified** item closed straight to `Done` (§1.4.1), the same comment replaces the
  "To verify" block with a one-line statement of why nothing is left for a human, plus the one
  thing that would reveal a regression:

  ```text
  Integrated on main — <one-line commit message> (<short sha>)

  Closed directly: <why no human verification applies — e.g. "backend filter change, no
  user-visible surface">. A regression would show as <the one observable symptom>.

  CR: docs/3-code-review/CR_NAD-<ID>.md
  Changelog: docs/2-changelog/NAD-<ID>.md
  ```
- **Blocked comment**: what is needed and from whom, in two or three lines. Never paste the
  question's full option list, logs, or a diff.

Never post: diffs, test output, logs, credentials, secret paths, raw provider responses, Codex
review bodies, or per-batch progress. The code review lives in git; Linear points at it.

### 1.6 Failure handling

If a Linear call errors: state `Linear: <operation> failed (<short reason>) — continuing`, and
carry on with the TRIP step. At the next transition, attempt the move again; skipping an
intermediate status is fine and is not worth reconciling.

### 1.7 Dependency gate: stop before starting a blocked item

The owner cannot always see Linear's blocking relations before naming an item. **This is the
first thing an agent does when told to work a specific item — before TRIP-1 discovery, before
TRIP-2 creates a worktree, before anything else.** Not a mid-flow check; if it is not the literal
first action, it is too easy to skip.

**Definition of blocked**: the item has a `blockedBy` relation to an issue whose status is not yet
`UAT`, `Done`, `Canceled`, or `Duplicate`. `UAT` counts as resolved on purpose — §1.4 already
established that `UAT` means the blocker's code is on `origin/main`, so a dependent item can
safely build on it before the owner's manual verification closes it to `Done`.

**Check (read-only, no Linear writes):**

```text
get_issue(id=<ID>, includeRelations=true) → relations.blockedBy
```

For each id returned, `get_issue(id=<blocker>)` to read its current status. Ignore `relations.
blocks`, `relatedTo`, and `parentId` entirely — none of those gate starting work; only `blockedBy`
does.

**If every blocker is resolved (or there are none)**: proceed normally.

**If any blocker is open**: stop. Do not plan, do not create a worktree, do not touch this item's
Linear status — it never started, so there is nothing to move to `Blocked` either. Then:

1. Report every open blocker: id, title, status.
2. For each open blocker, run the **same check one level deeper** — does *it* have open blockers
   of its own? A direct blocker sitting in `Ready for Development` is not necessarily startable;
   it may itself be blocked. Skipping this second level produces exactly the wrong suggestion —
   confirmed live in this backlog: an item blocked by three `Ready for Development` issues, all
   three themselves blocked by the same fourth issue, which was the one actually startable.
3. **Suggest**, do not switch: a blocker that is `Ready for Development` and has no open blockers
   of its own is a real answer — "`<id>` is what's actually stopping this, and it's ready to
   start." If no direct blocker qualifies (all further blocked, or already claimed — `In
   Progress`/`Code Review`/`Testing`/`Blocked`), look for a different, unrelated item instead:
   `list_issues(team=NADILI, state="Ready for Development")`, check a handful of candidates the
   same way, and offer the first one or two that come back clean. Which item to work next is
   always the owner's decision — present it, never switch automatically.

**No git-level backing.** Linear relation data is not reachable from a shell gate — the same
reason `In Progress` readiness needed `shared/scripts/check_item_ready.py` instead of a token-based API
call (§1.4): no Linear API token exists in this repository, and adding one was considered and
declined. The only defense here is placement — first action, every time, named in the
Step 0 of `plan.md` and Step -1 of `implement.md` — not enforcement.

---

## 2. Worktrees and concurrency

Several agents work this repository **simultaneously on one Mac**, each in its own git worktree,
against a continuously moving `origin/main`.

### 2.1 Starting work

The default is a dedicated worktree, created by the one supported command:

```bash
bash shared/scripts/worktree-new.sh <ITEM-ID>
```

`<ITEM-ID>` is the Linear identifier from §1.3 (e.g. `NAD-123`). It creates
`.codex/worktrees/<id-lower>` on branch `work/<ITEM-ID>` from `origin/main`, symlinks the primary
`venv/`, installs a real per-worktree `node_modules`, installs pinned Chromium, and runs
`shared/scripts/worktree-preflight.sh`. Accept the `work/<ID>` branch name; do not rename it to `feat/…`.

Fall back to a branch in the primary checkout only for docs-only work or a hotfix.

### 2.2 Rules that bind every phase

- **`origin/main` moving is normal, never an error.** Do not report it as a problem. Rebase
  before the testing gate and again at release:
  `git -C <worktree-path> fetch origin && git -C <worktree-path> rebase origin/main`.
- **Regenerate, never hand-merge, generated artifacts.** On conflict in
  `packages/contracts/openapi.json`, `requirements*.txt`, or a lockfile: take `origin/main`'s
  version and re-run the generator (`scripts/export_openapi.py`, `bash scripts/lock.sh`,
  `pnpm install`).
- **Alembic head collisions**: rebase, then re-point `down_revision` at the new head. Never merge
  two heads.
- **Ports are contended.** Assume defaults are taken. Prefer disposable stacks
  (`bash scripts/backend.sh check`, `compose.smoke.yaml`). If a port is busy, pick another —
  never kill another agent's process, never ask the owner.
- **Machine-global state**: `~/.config/nadili/local.env`, the local Compose stack, and the
  symlinked `venv/` are shared by every worktree. Never run `scripts/backend.sh purge`, reset the
  local database, or rewrite that env file unless the plan authorized it.
- **Shared venv**: Python dependency changes hit every worktree at once. Items needing different
  Python dependency sets cannot run in parallel — sequence them.
- **Stay inside your worktree.** Never check out another item's branch or edit outside scope.
- **`changelog_table.md` is candidate-owned.** Ticket integration does not edit it. The candidate
  stamper keeps all rows in descending version order and never drops another item's entry.
- **Every git command uses `git -C <worktree-path>`, not a bare `git`.** The session's shell cwd
  does not reliably persist across tool calls — a bare `git` can silently run against the primary
  checkout instead of your worktree. Treat a bare `git` as a bug, even right after a successful
  `cd`. `<worktree-path>` is **absolute** — `<primary-repo-root>/.codex/worktrees/<item-id-lower>`
  — never the relative `.codex/worktrees/<id>` form, which still depends on the very cwd this rule
  exists to stop trusting. `implement.md`, `plan.md`, and `release.md` use `<worktree-path>` as
  this shorthand throughout. This applies to read-only commands too, not only state-changing
  ones — a `git diff` read from the wrong tree doesn't error, it silently reviews the wrong code
  (observed on this repo: NAD-52's empty code review). `shared/scripts/hooks/git_guard.py` (wired via
  `shared/claude/settings.json`) blocks the highest-risk state-changing primary-checkout commands as a
  mechanical backstop, but it can't catch a misdirected *read*, and it fails open on anything it
  can't parse — it is not a substitute for this habit.

### 2.3 The primary checkout is shared ground

The non-worktree checkout belongs to no single item. Treat every write there as a write to a
resource other agents are using **right now**. It is the one place the worktree model does not
protect you, and therefore the one place that needs explicit rules.

- **Never assume which branch is current.** Before *any* commit in the primary checkout, assert it:

  ```bash
  [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "not on main — STOP"; }
  ```

  If it is not `main`, another agent is mid-flight — stop and report. Skipping this check puts your
  commit on a stranger's branch, where it merges under their message. **Observed on this repo**:
  NAD-27's plan commit landed on an unrelated config branch this way.
- **Never commit onto a branch you did not create.**
- **Never `git checkout`, `stash`, `reset`, or `clean` the primary checkout** to make your own
  command succeed. Those discard or hide in-flight work you cannot see.
- **Never `git add -A` there.** Stage explicit paths belonging to your item. `-A` sweeps up
  neighbours' untracked plans and pending deletions — an observed cause of a broken commit here.
  `shared/scripts/check_work_item_scope.py` now **rejects** the result: a commit may not stage files for
  two work items, and while on `work/<ID>` it may not stage another item's `docs/work/` files. It
  cannot see *how* you staged, only that the commit stopped belonging to one item — which is the
  damage that actually mattered. To drop a neighbour's file from the index without deleting it:
  `git restore --staged <path>`.
- **Leave it on `main`.** If you must create a branch in the primary checkout, return to `main` as
  soon as it merges. A long-lived branch parked there is what turns someone else's correct commit
  into a misplaced one.
- **Prefer a worktree even for process, config, and docs work.** Anything living longer than a
  single commit belongs in `.codex/worktrees/<slug>`, not on a branch parked in shared ground.
- **`index.lock` present means another agent is committing.** Wait for it to clear; **never delete
  it** — that corrupts a live process's commit.

### 2.4 Releasing from a worktree

`main` is checked out in the primary repo, so `git checkout main` **will fail** — expected. After
rebasing, integrate directly (a fast-forward by construction):

```bash
git -C <worktree-path> push origin HEAD:main
```

Then reconcile `main` and remove the worktree through the guarded harness script. It verifies the
exact target, clean state, and ancestry; it also drops a stale plan-only branch upstream before the
safe `branch -d`:

```bash
bash <worktree-path>/.claude/skills/nadili-process/scripts/release-cleanup.sh \
  <primary-repo-root> <worktree-path> <ITEM-ID>
```

---

## 3. Fix policy: P0/P1/P2

The severity scale and the full definitions live in
`shared/process/review-checklist.md` (§ Issue Severity Classification, § Fix
Policy) — outside the vendor's reach, so no upgrade can touch it. The rule in one line:

> **P0, P1 and P2 findings are fixed inside the TRIP cycle. Only P3 is deferred.**

Binding on every review surface — the Codex plan-review loop, the Codex code-review loop, and
manual `/TRIP-review`:

- `APPROVED` is returned only when no P0/P1/P2 remains open; P3 may stand recorded.
- Recheck P0/P1/P2 after fixes. Never re-flag a deferred P3 in a later round.
- P2 must be an actionable defect in the approved item, not unrelated cleanup or a new feature.
- Escalating P2 → P1 is allowed when it meets a P1 bullet; say which one. Downgrading a
  genuine P0/P1/P2 to avoid fixing it never is.

**If a TRIP upgrade reset the Codex prompt templates**, they will fall back to the vendor's
Critical/Major/Minor/Suggestion wording. The prompts still instruct Codex to take severity from
`checklist.md`, so the policy mostly survives — but re-apply the explicit P0/P1/P2 blocks per
`docs/6-memo/trip-upgrade-restore.md`.

---

## 4. Where plans live — and why Codex must be told

### 4.1 The convention

TRIP plans live with the rest of the work item, **not** in the vendor's `docs/1-plans/`:

```text
docs/work/<ID>/plan.md          e.g. docs/work/NAD-123/plan.md
```

One plan per work item; the file is edited in place across revisions rather than versioned by
filename. The plan declares a **release type**, not a version — `Release type: patch` (or `minor`
/ `major`), never `Planned version: x.y.z`. The concrete number is assigned at release time (§7).

Plans created before this convention stay in `docs/1-plans/` untouched; their code reviews
reference those paths. Do not migrate them.

### 4.2 A plan is born, reviewed, and committed inside its own worktree

**The plan never exists in the primary checkout at any point.** Planning enters the item's
worktree *before* the first line is written:

```bash
bash shared/scripts/worktree-new.sh <ID>     # idempotent: creates `work/<ID>`, or resumes it
cd .codex/worktrees/<id-lower>
```

Draft, Codex-review, revise, and commit there. On owner approval:

```bash
git -C <worktree-path> add docs/work/<ID>/plan.md
git -C <worktree-path> commit -m "docs(<ID>): add TRIP plan for <short feature name>"
git -C <worktree-path> push -u origin work/<ID>
```

Committing at approval is mandatory, not deferred to the release — the plan must be durable and
visible before implementation starts, and "approve now, build next week" must be safe. If the push
is rejected because a resumed session pushed first, `git -C <worktree-path> pull --rebase` and
retry — a normal collision. The gate runs on this commit too; never `--no-verify`.

Do not run `pre-commit-fast.sh` separately for a plan-only commit. Stage the plan and commit; the
hook selects the docs-only lane.

**Why the worktree rather than a careful procedure in shared ground.** The earlier design drafted
the plan in the primary checkout and required the agent to assert the branch, keep the tree clean,
and stage exact paths before committing. Every one of those steps was correct and every one was
skippable, so they got skipped: a plan landed on a neighbour's config branch; another sat
untracked for hours; a third was staged on `main` by one session while a different session was
mid-commit. Prose cannot make a multi-step ritual reliable. Being in the right directory from the
start removes the ritual instead of documenting it.

This also removes the old ordering hazard entirely. Previously the plan had to reach `origin/main`
*before* `worktree-new.sh` ran, because a worktree branches from the remote and untracked files do
not propagate into it — an unpushed plan was simply absent during implementation. Now the plan is
already on `work/<ID>`, which is the branch implementation continues on, so there is nothing to
propagate.

**Enforced, not merely expected.** `shared/scripts/check_work_item_scope.py` runs in `pre-commit.sh` and
`pre-commit-fast.sh`, and rejects any commit that stages `docs/work/<ID>/plan.md` while HEAD is
not `work/<ID>`. It lives in `shared/scripts/`, so unlike a rule written into a TRIP skill it survives
every `TRIP-upgrade`. The gate is mandatory; never `--no-verify`.

The plan commit is docs-only, so the installed hook routes it through the reduced docs lane in
`shared/scripts/pre-commit-fast.sh`; that lane must pass — see **Quality gate tiers** above.
If `origin/main` moved, rebase and retry; this is a normal concurrent-delivery event.

### 4.3 The vendor coupling, and the fix

The vendor's Codex prompts gate plan handling on a hardcoded path:

> `codex-implement/prompts/implement.tpl`: "If `{{TARGET}}` resolves to a file under
> `docs/1-plans/`, it is the **implementation plan**…"
> `codex-code-review/prompts/start.tpl`: "…If not a path, skip *Plan conformance*…"

Both files are classified "pure workflow" and are **rewritten wholesale by every
`TRIP-upgrade`**, so editing them is not a durable fix. A plan outside `docs/1-plans/` risks
Codex treating it as a free-form label — silently implementing and reviewing **without reading
the plan at all**. That is a correctness failure, not a cosmetic one.

**The durable fix is in the invocation, which we own.** Every codex script accepts trailing
`EXTRA_PROMPT` text. Always lead that argument with:

```text
TARGET is the implementation plan — read it in full and evaluate against it.
```

An explicit instruction in the prompt body overrides a path heuristic, whatever the vendor
template says after the next upgrade. Concretely:

```bash
# plan review
bash .claude/skills/codex-plan-review/scripts/start.sh \
    --prompt-file .claude/skills/codex-plan-review/prompts/start.tpl \
    docs/work/NAD-123/plan.md \
    "TARGET is the implementation plan — read it in full and evaluate against it."

# implementation
bash .claude/skills/codex-implement/scripts/start.sh \
    --prompt-file .claude/skills/codex-implement/prompts/implement.tpl \
    docs/work/NAD-123/plan.md \
    "TARGET is the implementation plan — read it in full and implement against it. Implement only: <batch>"

# code review (append to the gate summary)
bash .claude/skills/codex-code-review/scripts/start.sh \
    --prompt-file .claude/skills/codex-code-review/prompts/start.tpl \
    docs/work/NAD-123/plan.md \
    "TARGET is the implementation plan — read it in full and evaluate the diff against it. Review criteria, severity scale (P0/P1/P2/P3) and approval gate: .claude/skills/nadili-process/review-checklist.md. $GATE_SUMMARY"
```

**Never omit this sentence.** It is the single thing keeping plan conformance alive across
upgrades. If a Codex report shows it skipped plan conformance or never read the plan, this
sentence was missing — re-run with it rather than arguing with the review.

**The same coupling applies to the review criteria.** The vendor's `start.tpl` calls
`.claude/skills/TRIP-review/checklist.md` the "single source of truth"; that path is now a shim
pointing at `nadili-process/review-checklist.md`. The shim is deliberately blunt about
redirecting, but do not rely on the hop alone — **name the real criteria file in the code-review
invocation**, as above. Two independent pointers, so an upgrade that flattens one still leaves the
other. If a review comes back tagged Critical/Major/Minor/Suggestion, this clause was missing.

The scripts themselves are path-agnostic: `target_key()` derives state keys via `realpath` plus a
checksum suffix, so any path works and concurrent items never collide. **Anything locating a state
file by hand must derive the key through
`bash .claude/skills/codex-plan-review/scripts/key.sh <target>`** — an inline `realpath | sed`
misses the checksum suffix and silently fails to find the file (see `release.md` Step 3).

---

## 5. Owner interruption budget

The owner is the scarcest resource: they run several items in parallel. Interrupt as rarely as the
work genuinely allows — but **under-asking is its own failure**: a plan built on a guessed
requirement costs far more than the round that would have settled it.

- **TRIP-1**: skip discovery entirely when a Linear issue or existing spec already settles scope.
  Otherwise batch questions into rounds of up to 4 and keep going **while genuine blocking
  ambiguity remains**, bounded by the release-type ceiling in `plan.md` §1.2 (patch 1 / minor 3 /
  major 5) and endable at any point by the owner's escape-hatch option. The single planned stop is
  the plan-approval gate.
- **TRIP-2**: determine completion **mechanically** (all checkboxes crossed or recorded as
  out-of-scope, gate green, Codex `APPROVED`) and flow straight into release. Stop only for a
  real owner-only blocker.
- **TRIP-3**: **zero stops. Delivery is pre-authorized — never ask.** See §6.
- **Any phase**: **never** spend an interruption on permission to call a `codex-*` script, to send
  it the plan or the repository's code, or to let `codex-implement` write in the item's worktree.
  Pre-authorized once, for all phases — see "Standing Codex-delegation authorization" above.

Anything answerable from the code, `AGENTS.md`, `PRD.md`, `docs/architecture/`, or a Linear
specification is the agent's work, not the owner's.

---

## 6. Standing delivery authorization

**Delivery is pre-approved. TRIP-3 never asks the owner whether to commit, merge, push, or clean
up.** Granted by the repository owner; it does not expire with a session and applies to
every agent and runtime. Do not ask again, and do not re-introduce a confirmation prompt "just to
be safe".

The rationale is that the question protected nothing. Before Step 12 pushes, every
precondition has been proven mechanically:

- Step 11 ran `bash shared/scripts/integration-check.sh` on the final delivery commit,
- Codex returned `APPROVED` with no open P0/P1/P2,
- the plan's checkboxes are crossed or explicitly recorded as out of scope,
- `TRIP-2` already stopped and asked if anything was genuinely unresolved.

A prompt that can only be answered "yes" is not a safety control — it is latency. The real
controls are upstream and stay in force.

### 6.1 What TRIP-3 does instead

Run Steps 10-14 straight through, then **report**: the commit message, the CR and changelog
paths, the Linear transition, and the worktree that was removed. The owner stays
informed after the fact rather than blocking before it.

### 6.2 Safety rails that remain non-negotiable

Autonomy applies to the *decision to deliver*, never to the manner of delivering:

- **Never `git commit --no-verify`.** A red gate blocks the release, full stop.
- **Never force-push.** On rejection, re-fetch, rebase, retry.
- **Only fast-forward integration.** Never a merge commit; a non-fast-forward means rebase and retry.
- **Never rewrite published history**, never delete a remote branch other than the item's own,
  never touch another item's worktree or branch.
- **Never deliver on a red gate, an unresolved P0/P1/P2, or a `NEEDS_REWORK` verdict.** These are
  blockers, not decisions — and a blocker is reported, not confirmed away.

### 6.3 The one carve-out

If the **plan itself** records an explicit owner-confirmation requirement for release — for
example a destructive migration, a public-contract break, or anything the owner flagged at plan
time — honor it. That is a plan-level decision made deliberately in advance, not a default
prompt. Absent such a line, deliver.

---

## 7. Versions belong to candidates

**A plan declares a release type. It never declares a release number.**

```markdown
Release type: patch        # or: minor / major
```

Tickets integrate without reserving, stamping, or publishing candidate identifiers. The candidate
coordinator assigns the number only after it has the complete ticket membership and a clean `main`
checkout. This keeps ticket integration independent and lets the candidate own one immutable
source SHA.

### 7.1 Candidate reservation

`production-release.md` delegates reservation to `release-candidate.sh prepare`, which calls the
existing reservation helper after fetching the remote candidate state:

```bash
venv/bin/python scripts/next_version.py <release-type> --fetch --reserve <ITEM-ID>
```

The helper composes published candidates and live reservations. The reservation store is
`${NADILI_VERSION_RESERVE_DIR:-${NADILI_GATE_LOCK_DIR:-${TMPDIR:-/tmp}/nadili-gate-locks}/version-reservations}`;
entries hold the item id and ISO timestamp, and expire after `NADILI_VERSION_RESERVE_TTL` seconds
(default `7200`). Liveness is TTL-based, not PID-based, because one candidate spans many shells.

The same prepare operation runs `scripts/release_stamp.py` for the named ticket changelogs. It
stamps only candidate-owned aggregate fields, appends the existing artifact marker, stages the
three stamped files with the requested artifacts, commits, and freezes the source SHA. The
stamper is idempotent for a retried prepare with the same number.

### 7.2 Candidate order

The candidate sequence is deliberately separate from ticket integration:

1. start from clean `main` and freeze the source SHA;
2. reserve and stamp through `release-candidate.sh prepare`;
3. qualify the frozen tree with the full unreduced suite;
4. publish only the qualified candidate;
5. reconcile failed or abandoned attempts before another reservation.

Qualification and publication remain bound to the exact source SHA. A changed tree, a failed
capability, or a rejected candidate push requires a new or resumed coordinator attempt according
to `production-release.md`; ticket evidence is not reused as candidate evidence.

### 7.3 Collision backstop

If `publish` finds the reserved number no longer monotonic against freshly fetched tags, or the
atomic push is rejected, the attempt is retained as failed with a safe reason and **nothing is
rewritten**: a sibling candidate won that number. Prepare a new candidate — a fresh reservation,
a fresh stamp and a fresh qualification of the new frozen SHA — rather than amending the old
attempt. `publish` is idempotent for a lost push response: an existing remote tag at the expected
SHA recovers as success, a tag at a different SHA fails closed. Reservation cleanup is explicit
after a successful publication; TTL cleanup handles an abandoned attempt.

### 7.4 What a release type means here

- `patch` — a bug fix or an internal change with no new user-visible capability. The default.
- `minor` — new user-visible behavior, a new endpoint, a new surface. Backward-compatible.
- `major` — a break in a published contract: the OpenAPI surface, a CLI, or persisted data
  semantics. Rare, and the plan should say plainly what breaks.
