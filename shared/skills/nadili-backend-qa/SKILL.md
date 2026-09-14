---
name: nadili-backend-qa
description: Independently test an approved Nadili backend candidate through isolated API, job, CLI or operator behavior and report observed evidence before integration.
---

# Nadili Backend QA

For hotfix items, the recorded `hotfix.md` supplies scope, acceptance and routing wherever
this stage refers to the spec or plan. The stage obligations remain unchanged.

Read `AGENTS.md`, `shared/process/SKILL.md` Stage 5,
`shared/process/testing.md`, the pinned spec and plan. The coordinator dispatches
a **separate fresh-context tester agent** for this stage; the implementer cannot self-certify.
For a not-applicable route, verify the reason against the actual diff.
For gstack in Codex, use the `backend_qa` role and the model/risk/retest routing in
`adapters/gstack/orchestrator.md` § QA dispatch contract; never inherit the coordinator's model implicitly.

Exercise applicable behavior on an isolated runtime. Derive expected outcomes from the spec,
then observe HTTP/job/CLI results and persisted effects, including relevant failures, tenancy,
authz and replay. Report candidate fingerprint, environment, expected/observed results, evidence
and a pass/fail/blocked verdict under `Backend QA` in `docs/work/<ID>/verification.md`.
The tester does not change implementation, commit, move Linear or deliver. Return defects to
the implementer; retest the new reviewed candidate.
