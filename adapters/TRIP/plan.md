---
document_profile: agent-primary
canonicality: derived
owner: workflow
---

# Planning procedure (Nadili)

The TRIP implementation of `shared/process/SKILL.md` Stages 1–2. `/TRIP-1-plan` delegates here. This file is
project-owned and is never rewritten by `TRIP-upgrade`.

The main process in `shared/process/SKILL.md` wins. Legacy TRIP details live in
`adapters/TRIP/process.md`: §1 Linear lifecycle (incl. §1.7 dependency gate), §2 worktrees,
§3 P0/P1/P2 policy, §4 plan location, §5 owner interruption budget, §7 versions.

## Read first

1. `shared/process/SKILL.md` beside this file — the main Nadili process.
2. `adapters/TRIP/process.md` — TRIP-specific details.
3. `docs/ARCHI.md` — current system architecture.
4. `AGENTS.md` — canonical product, architecture and security invariants. `ARCHI.md` is derived;
   `AGENTS.md` wins on any conflict.
5. `docs/coding-standards.md` — **mandatory** before planning any Python, test, migration, or
   script change.

Conditionally, in full, only when the feature touches the area:

- `PRD.md` — engine product behavior (pipeline, Claims, Triage, Ask, Memory).
- `docs/web/PRD.md`, `docs/web/ARCHITECTURE.md`, `docs/web/DESIGN.md` — the public Nadili site.
- `docs/architecture/<NN>-*.md` + `docs/adr/` — the specific backend domain being changed.
- `packages/contracts/openapi.json` — any API schema or operation-ID change.
- `docs/runbooks/`, `infra/services/README.md` — provider operations or deployment behavior.

Historical, **not** truth: root `ARCHITECTURE.md`, `CLAUDE.md`, `docs/migration-agent-plans/`.

---

## Step 0: Dependency gate — before anything else

If the request names a Linear item, check whether it is blocked (`adapters/TRIP/process.md` §1.7) **before**
Step 1. A blocker resolves at `UAT`, not `Done` — code is already on `main` by then. If blocked:
stop, report every open blocker, and check one level deeper before suggesting one as a substitute
(a blocker in `Ready for Development` can itself be blocked). Offer the alternative; never switch
without the owner saying so.

---

## Planning boundary

Discussion happens before this procedure and writes nothing. Begin only after the owner's
explicit planning request. The old `--speedrun`/`--yolo` automatic implementation path is not
part of the Nadili process: reviewed planning ends at `Ready for Development` and waits for a
separate owner implementation start.

---

## Step 1: Discovery and clarification

**Interrupt as rarely as possible.** A question must earn its place: ask only when two readings of
the request would produce **materially different plans**. Anything answerable from `PRD.md`,
`AGENTS.md`, `docs/architecture/`, a committed repository specification, or the code is your work, not the
owner's.

### 1.0 Resolve it yourself first

- **Skip 1.1 entirely** when the request is already unambiguous — a Linear issue with a committed
  specification, an existing spec under `docs/`, or a request the docs already settle. Go to
  Step 2 and state assumptions in the plan's Overview instead of asking.
- **A bare issue id (e.g. `NAD-123`)** means chat-registered work per
  `docs/CHAT-TO-LINEAR-INTAKE.md`. Resolve the committed specification by the issue ID
  (`NAD-123-` filename) or the plan's `**Spec:**` path, and plan from that. A legacy
  `Spec: <path> @ <sha>` remains valid. Scope is frozen: **no discovery, no second
  issue, no editing the spec.** If the spec is wrong or incomplete, that is a `Blocked` stop.
- **Never ask** about anything a canonical document, `shared/scripts/`, or the code answers.
- If only a minor detail is open, take the sensible default, record it as an explicit assumption,
  and let Step 4's single gate catch it.

### 1.1 Clarify (only when genuinely blocking)

Summarize your understanding in 2-3 sentences, then use `AskUserQuestion`, batching up to 4
questions per call (one "round"). Never trickle questions one at a time across turns.

Frame around: **scope** (in vs out), **behavior** (from the user's view), **constraints**,
**priority** (if trade-offs are needed). Give 2-4 concrete options per question, grounded in the
codebase.

**Every question ships a recommendation.** Pick the option you would choose, put it **first**, and
append `(Recommended)` to its label. The owner is answering between four other items — a question
that makes them derive the answer from scratch costs more than it saves. If you genuinely have no
lean, say so in the option descriptions rather than faking one.

### 1.2 Alignment: keep asking until aligned

The goal is a **shared understanding**, not a fixed number of questions. Keep asking follow-up
rounds **while genuine blocking ambiguities remain**, and stop as soon as none do — never
manufacture questions to fill a quota. A ceiling caps the questioning if you are still finding
real gaps, scaled by the plan's release type:

| Release type | Ceiling (rounds of up to 4 questions) |
| ------------ | ------------------------------------- |
| patch        | 1                                     |
| minor        | 3                                     |
| major        | 5                                     |

When the ceiling is hit, summarize what you know, write the open assumptions explicitly into the
plan, and go to Step 2. Don't over-question: the Codex plan review (Step 3) backstops what
discovery misses, so lean toward proceeding rather than squeezing out every edge case.

**Owner escape hatch.** From the **second round onward**, include a standing option worded like
**"Use your recommendations for everything remaining → write the plan"**. If the owner picks it,
stop asking immediately, adopt your recommended answer for every still-open question, and go to
Step 2. The owner decides when alignment is enough — this lets them end it without waiting for the
ceiling.

After discovery ends (aligned, ceiling hit, or escape hatch), go **directly** to Step 2 — no
approach-confirmation question.

---

## Step 2: Plan document creation

Plans live at `docs/work/<NAD-ID>/plan.md`, **inside the item's own worktree** — not
`docs/1-plans/`, and never in the primary checkout (`adapters/TRIP/process.md` §4).

Order is ticket → worktree → draft:

1. Draft the content in memory (title + Overview at minimum).
2. Use the existing Linear issue if there is one; otherwise create it in `Specifying`
   (`adapters/TRIP/process.md` §1.3 — standing authorization, do not ask).
3. Enter the item's worktree. Idempotent — same command whether creating or resuming:
   ```bash
   bash shared/scripts/worktree-new.sh <ID>
   cd .codex/worktrees/<id-lower>
   ```
4. Write the plan there, `**Linear**: NAD-XXX` as the first line after the title. **Never propose
   a version number** — under parallel delivery a planned number is a guaranteed collision. Write
   `Release type: patch` (or `minor` / `major`); the release procedure assigns the number from the
   real tag list (`adapters/TRIP/process.md` §7).
   Record `Workflow adapter: trip` in new plans; existing plans without the line remain TRIP
   items. `shared/process/SKILL.md` defines the selection rule.
5. Write the mandatory `Verification:` line — the item's terminal status, decided now, by
   `adapters/TRIP/process.md` §1.4.1. Exactly one of:

   ```text
   Verification: UAT — <what the owner will look at and judge>
   Verification: Done — <why no human looking at the product changes the verdict>
   ```

   A plan without this line is incomplete. The release procedure (TRIP-3 Step 13) applies it
   verbatim; it is decided here, once, and not re-argued at release time.

For a new item, freeze the spec in the worktree after the issue gives it an ID. Everything after
this — drafting, Codex review, architecture review, revisions, the commit — happens in that worktree.
`shared/scripts/check_work_item_scope.py` runs in the pre-commit gate and **rejects** a plan committed
from any other branch, so this is enforced, not merely expected.

Legacy plans stay in `docs/1-plans/` — do not migrate them.

### Required sections

````markdown
# [Feature Name] Implementation Plan

**Linear**: NAD-XXX
Release type: patch
Verification: UAT — [what the owner will look at] | Done — [why no human check applies]
Backend QA: applicable/not applicable — [behavior, isolated runtime, expected outcome and evidence; or reason]
Frontend QA: applicable/not applicable — [browser journey, local app, viewport scope, expected outcome and evidence; or reason. Admin CRM-only defaults to desktop unless responsive CSS, shared layout/UI, or explicit acceptance requires more]

## Overview

[2-4 sentences describing the feature and its purpose]

## Problem Statement (if applicable)

[Current limitations/issues this feature addresses]

## Solution Architecture

[High-level design approach]

## Implementation Details

### 1. [Component/Module/File Name]

**File**: `path/to/file`

**Current state** (if modifying existing): [what exists now]

**Modifications**:

- Specific change 1 (around line X)
- Specific change 2 (around line Y)

## Technical Considerations

[Address every bullet that applies. Write "N/A" for the rest — a silently omitted bullet reads
as "not considered".]

- **Pattern Usage**: Which existing patterns to follow (from ARCHI.md). Layering is
  non-negotiable: routers validate/translate, services own use cases + transactions +
  authorization, repositories own SQL.
- **Contract & Client Impact**: Does this change OpenAPI schemas or operation IDs? If yes, the
  plan MUST land regeneration (`scripts/export_openapi.py`), `packages/client-ts`,
  `nadili_client`, and contract/client/backend/UI tests **in the same change**.
  `packages/contracts/openapi.json` is never hand-edited.
- **Schema & Migration Impact**: Alembic migration needed? FK `RESTRICT`/`CASCADE` choices,
  backfill and rollback, bounded pagination, workspace scoping of new tables/columns.
- **Tenancy & Public Boundary**: Every user-owned read/write scoped by active workspace. Public
  responses expose opaque public IDs only — never internal UUIDs, provider subjects, raw source
  content, pending Claims, or CRM diagnostics.
- **Security Invariants**: Auth fails closed; rate limiting and request-size enforcement stay on;
  credential values never leave the credential backend; all untrusted provider/user content is
  wrapped before any LLM call and every model response validated through a bounded safe parser;
  logging goes through `SecretRedactor`.
- **AI Cost & Job Impact**: New or changed model calls — pipeline stage, model tier, batching
  bounds, `ai_cost_log` recording. Jobs stay persisted, leased, retry-bounded, idempotent;
  provider cursors advance only in the same committed transaction as the ingestion receipt.
- **Import Boundaries**: Confirm the change survives `shared/scripts/check_boundaries.py`. Never weaken
  the checker to bypass a missing port.
- **Concurrency & Local Environment**: see the dedicated section below.
- **Edge Cases**: Tenant isolation, authorization denial, validation failure, idempotent replay,
  retry/timeout, partial failure, Valkey unavailable (correctness must survive it).

## Files to Modify/Create

1. `path/to/file1` (modify) - Purpose
2. `path/to/file2` (new) - Purpose

## Type Definitions (if applicable)

## Performance & Cost Impact (if applicable)

## Backward Compatibility (if applicable)

## Concurrency & Local Environment Impact

[Mandatory. Several agents work tickets **simultaneously on one Mac**, each in its own worktree
from `bash shared/scripts/worktree-new.sh <ITEM-ID>`, against a moving `origin/main`. Answer all five;
write "None" per line when the change genuinely cannot collide.]

- **Shared toolchain**: worktrees **symlink the primary `venv/`** and share the hash-locked
  requirement files, so any change to `requirements*.in`, `pyproject.toml` deps, `.nvmrc`, or
  `pnpm-lock.yaml` is **machine-global**. Flag it and name the re-setup commands
  (`bash scripts/lock.sh`, `bash setup.sh`, `pnpm install --frozen-lockfile`). If this item needs
  a *different* Python dependency set from other in-flight items, say so — the shared venv cannot
  provide that, and the item must be sequenced rather than parallelized.
- **Ports**: Any listening port (Next dev 3000/3001, API, Postgres, Valkey, Storybook 6006,
  Playwright)? Name each and how a busy one is handled. Never assume a default is free — prefer
  disposable stacks (`scripts/backend.sh check`, `compose.smoke.yaml`) or an explicit
  non-default port over the shared daily stack.
- **Shared local state**: `~/.config/nadili/local.env` and the local Compose stack are shared by
  every worktree. Flag anything that mutates them, resets the local database, or runs
  `scripts/backend.sh purge`.
- **Moving `main`**: assume `origin/main` advances during implementation — normal, not an error.
  Name high-collision files (`packages/contracts/openapi.json`, Alembic head, lockfiles,
  `docs/2-changelog/changelog_table.md`) and the resolution: rebase and regenerate, never
  hand-merge a generated file.
- **Cross-item conflicts**: which other in-flight items plausibly touch these files, and what
  ordering this item depends on.

### Interleaving

Required for items touching shared mutable state. Enumerate every affected resource and every
step that can observe it changing underneath the item:

| Resource | Why it moves under an item |
|---|---|
| `origin/main` | siblings integrate continuously |
| Published tags | a sibling release publishes between two of our steps |
| Version reservation store | TTL expiry, and siblings reserving |
| Heavy execution lease | one machine-wide holder |
| `~/.config/nadili/local.env` | machine-global, shared by every worktree |
| Local Compose stack and its ports | machine-global |
| The primary checkout | no single item owns it |

Use this table for the step-level enumeration:

| Resource | Step | What can change under us | Response |
|---|---|---|---|
| origin-main | integration | `origin/main` can advance | rebase and retry |

When the response to a mid-flight change is not obvious, refuse and retry rather than adapt. The
commit gate `shared/scripts/check_plan_interleaving.py` enforces this subsection from the effective plan
blob for commits that touch mapped shared-state paths; its path classification is deny-by-default.

## Test Impact

[2-5 bullets: which existing tests the change affects, what new logic needs tests, whether an
integration/E2E check applies. No test code — the implement procedure's gate consumes this.]

State the **Backend QA** and **Frontend QA** routes separately, even when one is not applicable.
For each applicable route name what the independent tester can run, the acceptance oracle,
failure states and how to observe the result. Decide from behavior, not changed file types:
an API change that changes a browser journey needs both routes. An applicable route without
an executable isolated environment is a planning blocker, not a task for owner UAT.

Name validation capabilities, not every executable that can provide them. Select the minimal
covering runners using `adapters/TRIP/process.md` **Gate coverage**; never list a wrapper beside its children.

## Documentation Impact

[Mandatory. Every document OUTSIDE the TRIP docs this feature will leave outdated, one line each
on what goes stale. "None" if none. The release procedure's Documentation Sync consumes this.]

Candidates — evaluate each:

- `PRD.md` — engine product behavior (pipeline stages, Claim model, Triage, Ask, Memory).
  Replace superseded behavior; do not keep it as history.
- `docs/web/PRD.md`, `docs/web/ARCHITECTURE.md`, `docs/web/DESIGN.md` — public site behavior,
  delivery architecture, IA/design.
- `AGENTS.md` — only when an invariant, import boundary, command, or runtime-ownership line
  actually changes. Keep under 300 lines.
- `docs/coding-standards.md` — a Python convention or gate expectation.
- `docs/architecture/<NN>-*.md`, `docs/adr/` — a backend architecture decision; add a new ADR
  rather than rewriting a decided one.
- `docs/ARCHI.md` — per `docs/ARCHI-rules.md`; Stage 4 reconciles implemented architecture
  before code review, and delivery confirms it remains current.
- `README.md` — prerequisites, setup, quality-gate commands, repository map, docs index, known
  limitations.
- `CONTRIBUTING.md` — contributor workflow or gates.
- `infra/services/README.md`, `docs/runbooks/*.md` — Compose runtime, operations, provider
  onboarding, backup/restore, API smoke.
- `packages/ui/README.md` — shared `@nadili/ui` surface.
- `docs/SECRETS.md`, `SECURITY.md` — credential handling or reporting.
- `docs/fetch-ux.md` — fetch and backfill UX.
- `CLAUDE.md` — orientation only; update only if a top-level claim in it becomes wrong.

**Do not** hand-edit generated artifacts (`packages/contracts/openapi.json`,
`requirements*.txt`) — regenerate them and note that here.

New or materially rewritten Markdown needs `document_profile` front matter and must pass
`venv/bin/python .agents/skills/document-audience/scripts/check_document_profile.py <paths>`.

## To-dos

### Phase 1: [Name] (omit the title if there is only one phase)

- [ ] Task
- [ ] Task

**Note**: one phase is enough for simple plans. Split only for complex, sequential work.
**Note — slice vertically, not horizontally**: when a feature needs several phases, make each one
a **thin end-to-end slice** (schema → service → minimal UI touch) that is verifiable on its own,
so the **first** phase already produces something exercisable. Do NOT structure phases as one
layer at a time ("all schema", then "all API", then "all UI") — that leaves nothing testable until
the end and makes course-correction expensive. Thinnest working path first; later phases thicken
it (more cases, edge handling, admin views, polish).
**Note**: no test code during planning — Test Impact only names what the gate will run and author.
````

### Quality standards

- **Zero ambiguity**: every step clear and actionable.
- **File-level specificity**: exact files and functions.
- **Architecture alignment**: conform to `ARCHI.md` patterns.
- **Risk assessment**: highlight failure points.
- **Terminal status decided here**: the `Verification:` line is present and reasoned, not copied.
  This is a planning decision on purpose — at plan time the user-visible surface of the work is
  the thing being discussed, so the call is cheap and clean. At release time the context is a
  large diff, a gate log and a code review, where the same call is noisy and easy to get wrong.

---

## Step 3: Codex second-opinion review

Always run it — no confirmation question, of any kind. That includes **not** asking permission to
send the plan and the repository code to the external Codex service: pre-authorized standing, see
`adapters/TRIP/process.md` "Standing Codex-delegation authorization". Satisfy any runtime sandbox/network prompt
yourself and continue. The owner gets exactly one decision point, and it comes after the review
(Step 4).

1. **Start** — the trailing sentence is **mandatory**, not decoration: the vendor prompt decides
   "is this a plan?" by testing for `docs/1-plans/`, and our plans live elsewhere, so without it
   Codex may review without ever reading the plan (`adapters/TRIP/process.md` §4.3):
   ```bash
   bash .claude/skills/codex-plan-review/scripts/start.sh \
       --prompt-file .claude/skills/codex-plan-review/prompts/start.tpl \
       docs/work/<ID>/plan.md \
       "TARGET is the implementation plan — read it in full and evaluate against it."
   ```
2. **Parse the trailing tag**: `APPROVED` → Step 4. `NEEDS_REWORK` → surface to the owner.
   `REQUEST_CHANGES` → continue.
3. **Address P0/P1/P2.** Quote each, push back on incorrect ones, and fix legitimate ones by
   editing the plan in place. Record and defer only P3. Correct the severity when a finding
   meets a different bullet in `shared/process/review-checklist.md`, naming why.
4. **Write implementer notes** (1-3 sentences): what you fixed, what you pushed back on and why,
   any owner decision or environment limit Codex should stop re-flagging.
5. **Resume**:
   ```bash
   bash .claude/skills/codex-plan-review/scripts/resume.sh \
       --prompt-file .claude/skills/codex-plan-review/prompts/resume.tpl \
       --notes "Fixed X. Pushed back on Y because Z. Owner decided W." \
       docs/work/<ID>/plan.md
   ```
   → back to 2.
6. **Review allowance** — follow the selected adapter and existing item budget. Do not restart
   or exceed an exhausted allowance without the owner amendment required by `shared/process/SKILL.md`.

Surface reviews verbatim. Keep edits scoped to findings. Reset the thread
(`reset.sh <plan-path>`) only if context is genuinely confused.

---

## Step 4: Owner review

First run `nadili-architecture` on the reviewed draft. Incorporate its decision or no-impact
verdict into the plan or focused ADR, and repeat relevant plan review if the design changed.

Present a summary: feature, approach (1-2 sentences), files affected (count + key ones),
complexity, Codex status.

Use **one `AskUserQuestion`** for the owner's final plan decision:

- **Question**: "Review the plan at `docs/work/NAD-XXX/plan.md` (NAD-XXX — <feature>, <release
  type> release). How to proceed?"
- **Options**:
  - "Approved — ready for development" → commit the plan and stop before implementation
  - "Rework" → the owner gives feedback as text

Handle the answer:

- **Approved** — in this order:

  1. **Commit and push from the worktree** — follow the canonical command sequence in `adapters/TRIP/process.md`
     §4.2, which owns the mandatory timing, exact paths, push-collision retry, and gate rules.
  2. Verify the plan names the committed spec path and both files are on the pushed branch;
     move the issue to
     `Ready for Development` (`shared/process/SKILL.md` Stage 2). No comment.
  3. Stop. The owner separately starts implementation.

- **Rework**: update from the feedback, re-present. Another Codex pass if the changes are
  substantive. The issue stays where it is.
- **Other (custom input)**: handle accordingly.

Plan approval is not implementation authorization.

---

## No code during planning

Describe WHAT, WHERE and WHY — never actual implementations or detailed algorithm code. Code
belongs to the implement procedure.

---

## Guidance by change type

Use the sections matching what the feature actually touches.

### New / changed API endpoints

- Which router file, and the exact **stable operation ID** (it drives the generated client).
- Request/response schemas in `apps/api/schemas/` — public IDs only, bounded pagination and body
  size.
- Which service owns the use case, and where the transaction boundary sits.
- Authorization: identity Admin vs workspace owner vs public; how it fails closed.
- Private Admin/CRM surface or public engine surface (`routers/public_engine.py`,
  `public_openapi.py`)? The public surface serves finalized client-safe projections only.
- Contract fallout: regenerate OpenAPI, update `client-ts` + `nadili_client`, run
  `check_openapi_contract.py` — all in this change.

### Service layer

- The use-case boundary and which repositories it composes.
- Transaction scope: what must commit atomically (especially provider cursor + ingestion receipt).
- Idempotency and duplicate protection for state-changing operations.
- Authorization decisions belong here — not in routers, not in repositories.
- No SQL in this layer.

### Repository / database

- Alembic migration: upgrade + downgrade, FK `RESTRICT`/`CASCADE`, index strategy.
- Workspace scoping on every user-owned query and write.
- Backfill plan and its cost at current data volume.
- Parameterized SQL, sanitized search input, bounded limits.
- Whether the Alembic head will collide with concurrent items.

### AI pipeline

- Which stage, and where it sits in the cost order (deterministic → cheap model → capable model).
  New stages must be per-item inspectable and idempotent.
- Model tier, batching bounds, expected `ai_cost_log` impact.
- Untrusted-content wrapping before the call; the bounded safe parser validating the response.
- Prompt/schema versioning and how existing persisted results stay interpretable.
- Whether the project's private prompt-evaluation suite should gate the change.

### Background jobs / scheduler

- Job type, payload shape, lease duration, retry bound, terminal states.
- Idempotency on replay; dedupe key (provider ID / content hash).
- Cursor advance rules and the transaction it commits within.
- Scheduler stays singleton; manual work outranks background work.

### Provider adapters (Telegram / Bluesky)

- Named network/SDK exceptions caught; programming errors propagate.
- Rate-limit and backoff behavior against the provider.
- Credential access path (credential backend only — never Postgres, never logs).
- Which opt-in integration tests cover it and how they skip cleanly without credentials.
- Runbook impact (`docs/runbooks/telegram.md`, `bluesky.md`).

### Admin Web / public web

- Which app (`apps/admin-web` = the only Admin CRM surface; `apps/web-relocation` = public
  Nadili), and whether the component belongs in shared `@nadili/ui`.
- Data access strictly through `@nadili/client-ts` — never a direct backend import.
- Auth surface: Clerk-managed cookies only; no token in localStorage/sessionStorage/logs.
- Loading, empty and error states; accessibility; responsive breakpoints.
- Playwright E2E impact when selectors or flows change; dev-server port needs.

### Shared libraries (`core/`, `nadili_runtime/`)

- `nadili_runtime` is **stdlib-only**; `core` must not import `apps.api`, FastAPI, or SQLAlchemy.
- Every consumer of the changed symbol across backend, clients, and scripts.
- Whether this belongs in shared code at all, or is really backend-local.
