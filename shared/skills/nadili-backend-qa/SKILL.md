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

Temporary runtimes under a heavy lease must use `shared/scripts/gate_executor.py run` with an explicit
positive finite `--run-timeout` sized for the scenario. Stop every owned helper on success,
failure, blockage and interruption, then verify the holder is gone (or remains the outer owner's)
and the private lock is acquirable. A manual stop-marker loop alone is not bounded teardown;
helpers must route SIGINT/SIGTERM through their normal resource teardown; release resources before
returning the report. The deadline never substitutes for normal cleanup.

For upgrade/install changes, exercise the transition from the supported predecessor, not only a
clean fixture. Use non-secret observed metadata to reproduce relevant versions, ownership, modes
and retained state with synthetic data; identify unverified assumptions. Establish fixture modes
explicitly rather than relying on ambient umask. Never copy production credentials or data into QA;
this extends relevant scenarios within the existing QA stage, not a new gate or production probe.

Exercise applicable behavior on an isolated runtime. Derive expected outcomes from the spec,
then observe HTTP/job/CLI results and persisted effects, including relevant failures, tenancy,
authz and replay. Report candidate fingerprint, environment, expected/observed results, evidence
and a pass/fail/blocked verdict under `Backend QA` in `docs/work/<ID>/verification.md`.
The tester does not change implementation, commit, move Linear or deliver. Return defects to
the implementer; retest the new reviewed candidate.
