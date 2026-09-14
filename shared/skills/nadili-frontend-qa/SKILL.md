---
name: nadili-frontend-qa
description: Independently exercise an approved Nadili frontend candidate in a real browser and report observed evidence before integration.
---

# Nadili Frontend QA

For hotfix items, the recorded `hotfix.md` supplies scope, acceptance and routing wherever
this stage refers to the spec or plan. The stage obligations remain unchanged.

Read `AGENTS.md`, `shared/process/SKILL.md` Stage 6,
`shared/process/testing.md`, the pinned spec and plan. The coordinator dispatches
a **separate fresh-context tester invocation** for this stage; the implementer cannot
self-certify. For a not-applicable route, verify the reason against the actual diff.
For gstack in Codex, use the `frontend_qa` role and the model/risk/retest routing in
`adapters/gstack/orchestrator.md` § QA dispatch contract; never inherit the coordinator's model implicitly.

Exercise applicable browser journeys and relevant loading, empty, error and denied states against
an isolated local app. For Admin CRM-only work, test the planned primary desktop viewport; omit
narrow/mobile and general responsive inspection unless the candidate changes responsive CSS,
shared layout/UI, or the spec/plan explicitly requires it. Public/client UI and
responsive-impacting work keep relevant responsive coverage. Do not expand the recorded viewport
scope without candidate evidence. For a gstack item, `qa-only` is allowed only when its
effective checkpoint mode is `explicit`, the target is a local disposable URL, and its
preamble/browser need no owner onboarding, sign-in or setup. Otherwise use project
Playwright/browser tools. Gstack
supplies browser QA methods, not the independent agent role. Do not use `qa`, which fixes and
commits. Report the candidate, environment,
expected/observed behavior, evidence and verdict under `Frontend QA` in
`docs/work/<ID>/verification.md`. The tester does not change code, commit, move Linear or
deliver. Return defects to the implementer; retest the new reviewed candidate.
