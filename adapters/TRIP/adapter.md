---
document_profile: agent-primary
canonicality: derived
owner: workflow
---

# TRIP adapter

This adapter remains available for existing TRIP items. Nadili's stable stage skills call this
mapping before using a compatible TRIP method. It implements `shared/process/SKILL.md` using the
existing TRIP entrypoints and keeps the current delivery path unchanged while another adapter
is prepared. The vendor TRIP files and the shims in `adapters/TRIP/shims/` remain installed and checked by
`scripts/check_trip_shims.py`.

| Nadili boundary | TRIP implementation |
|---|---|
| Plan | `nadili-plan` may use `TRIP-1-plan` via `adapters/TRIP/plan.md`; the plan review uses `codex-plan-review`. The old speedrun and spec-only readiness rules do not apply. |
| Architecture | Before final plan approval, use the draft plan's technical review and canonical ADR process to resolve architecture. Do not write planned design into `docs/ARCHI.md` as implemented fact. |
| Implement | `TRIP-2-implement` delegates to `adapters/TRIP/implement.md` and its Codex implementation worker. |
| Code review | Reconcile implemented architecture into `docs/ARCHI.md` before the Codex review loop applies `shared/process/review-checklist.md`. |
| Backend QA | Dispatch an independent tester under `shared/process/SKILL.md` Stage 5 for applicable isolated HTTP, job, CLI and persisted-effect scenarios. TRIP-test is a test-authoring reference, not an independent runtime QA role. Record the verdict in `docs/work/<ID>/verification.md`. |
| Frontend QA | Dispatch an independent fresh-context tester invocation under `shared/process/SKILL.md` Stage 6 for applicable browser journeys. Use project Playwright/browser tools and record the separate verdict in the same verification file. |
| QA defects | The implementer fixes QA defects, refreshes affected code review, then returns a new candidate for affected-route retesting. `adapters/TRIP/implement.md`'s automated gate and `shared/process/testing.md` remain required. |
| Integrate | `TRIP-3-release` delegates to `shared/process/integrate.md`. Its name means **ticket integration**, not a production release. |
| Candidate | `../production-release.md` remains a separate coordinator. |

Add `Workflow adapter: trip` to newly created plans. Existing plans without the line remain
TRIP items for compatibility. The legacy phase files contain TRIP-specific instructions; they
are subordinate to `shared/process/SKILL.md` if wording diverges. Do not change their steps as part of
merely selecting another adapter.
