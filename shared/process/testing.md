# Testing and independent QA (Nadili)

For item delivery, start with **Item verification record** below: Stages 5–6 require observed
backend and frontend behavior from an independent tester where applicable. The automated-test
guidance here supports that work but cannot replace either QA verdict. Use the test-authoring
sections for implementation coverage or standalone backfill.

This file lives in `nadili-process/` and is **never rewritten by `TRIP-upgrade`**. The vendor's
`.claude/skills/TRIP-test/SKILL.md` is a shim pointing here, enforced by
`scripts/check_trip_shims.py`.

For hotfix items, `docs/work/<ID>/hotfix.md` supplies the scope, acceptance and QA routes
where this protocol refers to the spec or plan. Runtime evidence obligations are unchanged.

## Read first

1. `docs/ARCHI.md` — system architecture.
2. `docs/4-unit-tests/TESTING.md` — testing guidelines.
3. `docs/coding-standards.md` (§ Tests) — canonical test conventions and FK fixture ordering.

---

## Testing guidelines

### Scope

- Only run tests for relevant files that changed (not the whole project)
- Focus on the new feature/fix/refactor

### Commands

```bash
# Backend + workflow suites (default excludes integration)
bash shared/scripts/test.sh

# Everything, including opt-in integration coverage
bash scripts/test-all.sh

# A specific file, class, or test
venv/bin/python -m pytest apps/api/tests/test_claim_admission.py -m "not integration" -q
venv/bin/python -m pytest apps/api/tests/test_claim_admission.py::test_accepts_grounded_claim -q

# Backend tests against a disposable PostgreSQL + Compose config
bash scripts/backend.sh check

# Standalone Python client (focused micro-gate; also covered by pre-commit.sh)
bash scripts/client.sh

# Web: one package, or the whole web gate
corepack pnpm --filter @nadili/admin-web test
corepack pnpm --filter @nadili/web test
corepack pnpm --filter @nadili/ui test
corepack pnpm --filter @nadili/client-ts test
bash shared/scripts/web-check.sh fast          # add `full` to also build both apps

# Admin Web end-to-end (Playwright)
bash scripts/admin-web-e2e.sh

# Coverage (not wired into a gate; no enforced threshold)
venv/bin/python -m pytest apps/api/tests --cov=apps/api --cov-report=term-missing -m "not integration"
```

Run only what the change touched. `bash shared/scripts/pre-commit.sh` is the full local gate;
`bash scripts/pre-release.sh` is the release gate and belongs at release time, not in a tight
authoring loop.

**Concurrency**: several agents run tests on this Mac at once. Use disposable databases
(`scripts/backend.sh check`, `compose.smoke.yaml`) rather than the shared daily stack, never run
`scripts/backend.sh purge`, and expect Playwright/dev-server ports to be occupied — pick a free
port instead of killing another process.

### Test structure

```text
apps/api/tests/              backend tests (~96 files) — the primary suite
  conftest.py                shared fixtures
  fixtures/                  fixture data
  test_<area>_<concern>.py   naming pattern, mirrors the service/router/repository under test
shared/tests/unit/                  core/, nadili_runtime/, and scripts tests
tests/integration/           opt-in, credential-requiring coverage
apps/admin-web/src/**        Vitest colocated with components (vitest.config.ts)
apps/admin-web/e2e/          Playwright specs (playwright.config.ts)
apps/web-relocation/tests/   Vitest for the public site
```

Conventions:

- Markers: `integration` (real external APIs/DBs) and `slow` (expensive AI calls). The default
  run is `-m "not integration"`; integration tests must **skip cleanly** without credentials.
- Test names read as behavior: `test_<action>_<condition>_<result>`.
- Tests are deterministic — no real network, clock, randomness, or credentials unless explicitly
  marked integration.
- `assert` is allowed in tests (`S101` is per-file-ignored for `shared/tests/**` and `apps/api/tests/**`).
- Fixtures stay minimal and valid, **create rows in FK order**, and make workspace ownership
  explicit. See `docs/coding-standards.md` — FK fixture ordering has caused gate failures.
- Tests run under strict mypy where they live in `shared/tests/unit/`; annotate accordingly.

### Testing priorities

**Every feature must cover** (per `docs/coding-standards.md`): happy path, **tenant isolation**,
**authorization denial**, validation failure, **idempotency on replay**, and the relevant
failure/retry behavior. A bug fix starts with a failing regression test.

**Unit Tests**:

- Services — use-case logic, authorization decisions, transaction boundaries
- Repositories — workspace scoping on every query and write, FK behavior, pagination bounds
- Routers — schema validation, status codes, public-ID-only responses
- AI pipeline — stage transitions and documented fallback behavior, schema validation,
  batching bounds and safe parsers against malformed model output
- Jobs — leasing, retry bounds, idempotent replay, cursor advance transactionality
- Shared libs — `core/` models, permalinks, credentials; `nadili_runtime/` log redaction

**Integration Tests** (opt-in, skip without credentials):

- Real Telegram and Bluesky adapters
- Real AI provider calls
- Postgres-backed pipeline runs (`test_claim_pipeline_postgres.py` style)
- `scripts/smoke-api.sh` (real HTTP + Clerk sign-in), `scripts/portability-smoke.sh`

**Contract Tests**:

- `scripts/export_openapi.py --check` and `scripts/check_openapi_contract.py` after any schema
  or operation-ID change
- `@nadili/client-ts check` and `bash scripts/client.sh` for the generated clients. `client.sh` is
  a leg of `pre-commit.sh`, but not of `pre-commit-fast.sh` — so it still has to be run directly
  during implementation, and only becomes redundant once the full local gate runs at release.

**E2E**:

- Playwright for Admin Web flows and selectors (`bash scripts/admin-web-e2e.sh`)

**What to Test**:

- Happy path, then the mandatory tenant/authz/validation/idempotency set above
- Error states: named exception translation, bounded retries, graceful failure
- Edge cases: empty vs absent (`0`, `False`, `[]`, `""` are valid inputs), boundary limits
- Security behavior: fail-closed auth, redacted logging, no secret or internal ID leakage
- Resilience: correctness when Valkey is unavailable

---

## Hard-to-test code

Seam ladder, cheapest first: **exported pure helper → injectable client/adapter → module mock →
integration/emulator test**. Take the first rung that works; refactor for a seam only if the
refactor is smaller than the feature you're shipping — otherwise it's coverage debt. Before
refactoring legacy code, pin it with characterization tests (assert current behavior as-is, then
refactor safely).

Uncovered risky paths: one line each in `docs/4-unit-tests/COVERAGE-DEBT.md`
(`path | why hard | escape plan`). Delete a ledger line in the same change that gives its path
meaningful coverage.

---

## Item verification record

Stages 5 and 6 of `shared/process/SKILL.md` require **independent agent QA** for applicable backend and
frontend behavior after code review and before integration. Decide applicability in the plan, then confirm it
against the actual diff. Backend QA exercises isolated service/job/CLI behavior; Frontend QA
exercises real browser journeys. Admin CRM-only work uses its planned primary desktop viewport by
default; narrow/mobile and general responsive inspection apply only when responsive CSS, shared
layout/UI, or explicit acceptance makes them relevant. Public/client UI keeps relevant responsive
coverage. Unit, component and E2E suites support these verdicts but do
not replace observed runtime behavior. A tester starts with the pinned spec, plan and frozen
candidate, independent of the implementer; the implementer fixes defects and the tester
retests the new candidate. Create
`docs/work/<ID>/verification.md` in the item's worktree. Reconcile it with rerun results when
fixes or a rebase invalidate earlier evidence. Keep it bounded and factual; do not copy logs
or include credentials. Mark a QA route not applicable only with a reason grounded in the plan
and actual diff. A ticket has no version number.

```markdown
---
document_profile: agent-primary
canonicality: working
owner: workflow
---

# Verification — NAD-<ID>

Candidate: <commit or working-tree fingerprint tested>
Backend tester: <agent role and fresh invocation identifier, or not applicable>
Frontend tester: <agent role and fresh invocation identifier, or not applicable>

## Backend QA

- Applicability: applicable/not applicable — <reason checked against actual diff>
- Runtime: <isolated service/job/CLI boundary, setup, data isolation>
- <scenario from pinned spec/plan> — expected: <outcome>; observed: <outcome>;
  evidence: <bounded local response/persisted-effect reference>; pass/fail
- Verdict: pass/fail/blocked/not applicable

## Frontend QA

- Applicability: applicable/not applicable — <reason checked against actual diff>
- Runtime: <local app, backend or fixtures, browser and planned viewport scope>
- <browser journey/state> — expected: <outcome>; observed: <outcome>;
  evidence: <local report/screenshot reference>; pass/fail
- Verdict: pass/fail/blocked/not applicable

## Defects and Retest

- <route, defect, reproduction, owner fix, retest candidate/result, cross-route impact; or None>

## Checks

- `<exact command>` — passed/failed/skipped; <count or short evidence, reason if skipped>

## Architecture and Documentation

- `docs/ARCHI.md`: updated/unchanged — <reason>; estimated tokens: <count>
- <other canonical documents changed or confirmed unaffected>

## Limits

- <unresolved non-blocking limit, or None; an untested applicable runtime blocks delivery>
```
