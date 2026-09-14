---
document_profile: agent-primary
canonicality: derived
owner: workflow
---

# Code review checklist (Nadili)

The **single source of truth** for code-review criteria, severity classification, and the approval
gate. Every review surface applies the criteria below — referenced, never copied — so the surfaces
cannot drift:

- the TRIP Codex code-review loop (`.claude/skills/codex-code-review`), pointed here by the
  invocation's `EXTRA_PROMPT` (`adapters/TRIP/process.md` §4.3);
- the gstack Stage 4 one-shot runner (`shared/scripts/codex_code_review.py`);
- the manual audit path (`shared/process/review.md` beside this file);
- the Codex plan-review loop, for severity vocabulary only.

This file lives in `nadili-process/` and is **never rewritten by `TRIP-upgrade`**. The vendor's
`.claude/skills/TRIP-review/checklist.md` is a shim pointing here, enforced by
`scripts/check_trip_shims.py`.

## Systematic review checklist

### 1. Functional Requirements

- [ ] Implementation logic matches requirements correctly
- [ ] Interface/API matches documented specifications
- [ ] Error scenarios handled with proper feedback
- [ ] Edge cases and boundary conditions validated

### 2. Code Quality

- [ ] Proper typing (no unjustified dynamic types)
- [ ] DRY principle - no code duplication
- [ ] KISS principle - not unnecessarily complex
- [ ] Consistent, descriptive naming conventions
- [ ] Complex logic has explanatory comments
- [ ] Files/modules not excessively large
- [ ] Imports/includes organized, unused ones removed

### 3. Architectural Compliance

- [ ] Code follows established patterns from ARCHI.md and the invariants in `AGENTS.md`
- [ ] Layering respected: routers validate/translate only, services own use cases +
      authorization + transactions, repositories own SQL
- [ ] No domain decisions in routers; no SQL in services; no backend imports in web clients
- [ ] `shared/scripts/check_boundaries.py` passes, and the checker was not weakened
- [ ] `nadili_runtime` stayed stdlib-only; `core` free of `apps.api` / FastAPI / SQLAlchemy
- [ ] Conventions in `docs/coding-standards.md` followed (naming, typing, error translation)
- [ ] No hidden fallback, silent no-op, speculative abstraction, or fake production path
- [ ] Change is one complete vertical slice; unrelated code left untouched

### 4. Tenancy & Public/Private Boundary

- [ ] Every user-owned query and write is scoped by the active workspace
- [ ] Public responses expose opaque public IDs only — no internal UUIDs, no provider subjects
- [ ] No raw source content, pending Claims, or CRM diagnostics leak into a client contract
- [ ] Admin vs workspace-owner authorization distinction is correct and fails closed
- [ ] Claims remain the sole factual knowledge/review unit (no Card or MemoryItem revival)

### 5. Contract & Data Integrity

- [ ] If schemas or operation IDs changed: OpenAPI regenerated, `client-ts` and `nadili_client`
      updated, and contract/client/backend/UI tests run — all in this change
- [ ] `packages/contracts/openapi.json` and `requirements*.txt` were regenerated, never hand-edited
- [ ] Alembic owns any schema change; `down_revision` points at the current head; FK
      `RESTRICT`/`CASCADE` chosen deliberately; no runtime schema mutation
- [ ] SQL parameterized, search input sanitized, pagination and body sizes bounded

### 6. Jobs, Pipeline & AI Safety

- [ ] Jobs stay persisted, leased, retry-bounded, and idempotent; scheduler stays singleton
- [ ] Provider cursors advance only in the same committed transaction as durable ingestion
- [ ] Replays dedupe by provider ID / content hash
- [ ] All untrusted provider/user content wrapped before any LLM call
- [ ] Every model response validated through a bounded safe parser; model calls recorded in
      `ai_cost_log`
- [ ] New pipeline stages are cost-ordered, per-item inspectable, and idempotent
- [ ] Correctness survives total Valkey loss (cache is derived state only)

### 7. Concurrency & Delivery Hygiene

- [ ] Review base is pinned for the cycle; no merge commit introduced. Current `origin/main`
      freshness belongs to Stage 7 integration, not repeated code-review findings
- [ ] Generated artifacts regenerated rather than hand-merged after any rebase
- [ ] No machine-global side effects on shared local state (`~/.config/nadili/local.env`,
      the shared Compose stack, the local database) beyond what the plan authorized
- [ ] No hardcoded default ports assumed free; no other agent's process or branch touched

### 8. Error Handling

- [ ] Only named exceptions the layer can translate are caught; programming errors propagate
- [ ] Causal chains preserved with `raise ... from exc`
- [ ] Adapters catch named network/SDK errors only
- [ ] Retries are bounded and reserved for explicitly transient failures
- [ ] Error messages are clear and actionable; failure modes are graceful
- [ ] Logs carry a safe error code and `type(exc).__name__` — never a raw provider response,
      user content, token, secret path, or `repr(exc)`

### 9. Security

- [ ] Input validated at the boundary; rate limiting and request-size enforcement left on
- [ ] Auth and workspace lookup fail closed
- [ ] Clerk session stays in Clerk-managed cookies — never localStorage/sessionStorage/logs/DB
- [ ] Credential values never leave the credential backend; PostgreSQL holds references only
- [ ] Secret values and paths absent from responses, jobs, logs, cache, audit, `repr`, CLI args
- [ ] All logging passes through `SecretRedactor`
- [ ] CORS credentials still false; forwarded IPs trusted only from `TRUSTED_PROXIES`
- [ ] No `noqa` / `type: ignore` / gate exclusion added to hide a real problem

### 10. Performance & Cost

- [ ] No obvious performance issues; appropriate data structures used
- [ ] Queries bounded and indexed; no N+1 across workspace-scoped reads
- [ ] AI cost accounted for: right model tier, batching bounds respected, `ai_cost_log` recorded
- [ ] Resource cleanup implemented (connections, leases, no leaks)
- [ ] No unnecessary operations in hot paths

---

## Issue Severity Classification

Every finding — from a human review, the Codex code-review loop, or the Codex plan-review
loop — carries exactly one of these tags. This is the **only** severity vocabulary in the
workflow; do not use Critical/Major/Minor/Suggestion or P4+.

**P0 — Blocks delivery. Fix now.**

- Security vulnerabilities; authentication or authorization bypass
- Data corruption or data loss
- Tenancy leak: a query or write not scoped by active workspace
- Public/private boundary leak: internal UUIDs, provider subjects, raw source content, pending
  Claims, or CRM diagnostics reaching a client contract
- Credential or secret value reaching a response, job, log, cache, audit record, or CLI arg
- Unwrapped untrusted content sent to a model, or an unvalidated model response
- Breaking API/contract change shipped without regenerated contract + clients
- Build, gate, or migration failure

**P1 — Blocks approval. Fix now.**

- Incorrect business logic or wrong results on realistic inputs
- Missing or wrong error handling on a path that can actually fail
- Broken idempotency: unsafe replay, cursor advanced outside the ingestion transaction
- Significant performance or AI-cost regression
- Architectural violation: layering breach, import-boundary breach, weakened checker or gate
- New logic shipped with no test and no coverage-debt entry

**P2 — Blocks approval. Fix in this item.**

- An in-scope maintainability defect with a concrete consequence: e.g. duplicated policy
  already diverges, or a misleading name causes an incorrect caller. Naming, duplication or
  complexity alone is not sufficient; cite the affected behavior or required operation
- Missing documentation or comments needed to operate or extend this change
- Lower-impact but reachable edge case omitted from the approved behavior

**P3 — Record, do not fix.**

- Performance micro-optimizations
- Readability, naming, extraction and style preferences without a concrete defect
- Additional test coverage beyond the critical-path floor

---

## Fix Policy: P0, P1 and P2

**P0, P1 and P2 findings are fixed inside one Nadili item.** A reviewer must report an
actionable P2 even when no P0/P1 exists. This applies to every review surface.

- **P0 / P1 / P2** — fix in this change, or push back with a concrete technical rationale.
  Each blocks `APPROVED` while open. Recheck each on the next review pass.
- **P3** — record with disposition `open — deferred (P3)`; do not fix or re-flag it in later
  rounds. A useful larger cleanup outside approved scope belongs in a separate item.
- **Scope discipline still applies**: a P2 must identify a concrete defect in this item, not
  demand a new feature, speculative edge-case handling, or unrelated cleanup. State the triggering
  condition, violated requirement and concrete consequence. Do not manufacture a P2 to avoid
  returning an empty finding list.

**Severity correction is allowed but must be explicit.** If a P2 meets a P0/P1 bullet,
re-tag it and say why. Do not downgrade a genuine P0/P1/P2 to avoid the fix.

---

## Review Completion Criteria (Approval Gate)

Minimum for approval:

- [ ] All functional requirements implemented
- [ ] **No open P0, P1 or P2 findings** (P3 may remain recorded and deferred)
- [ ] Lint and type-check clean on the touched backend paths, and the affected web packages pass
      their lint/typecheck/test; `shared/process/gates.md` owns scheduling and the runner scripts own exact flags
- [ ] `venv/bin/python shared/scripts/check_boundaries.py` passes and the checker was not weakened
- [ ] Affected `pytest` targets pass with `-m "not integration"`
- [ ] Every contract/integration trigger that applies was run (`adapters/TRIP/implement.md` § Testing gate,
      step 3): OpenAPI + generated clients, Alembic, E2E, portability, document profile
- [ ] New logic has test coverage (or a coverage-debt ledger entry per the hard-to-cover policy)
- [ ] Documentation updated per project standards

Before a release, `bash scripts/pre-release.sh` is the authoritative release gate.
`bash shared/scripts/pre-commit.sh` remains the full local gate. No approval may
rest on a gate that was skipped, split, weakened, or bypassed with `--no-verify`.
