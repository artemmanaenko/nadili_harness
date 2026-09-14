---
name: nadili-deliver
description: Integrate a reviewed and independently QA-tested Nadili item into main without a PR, then update Linear and clean its worktree; never run a vendor ship workflow.
---

# Nadili ticket integration

For hotfix items, the recorded `hotfix.md` supplies scope, acceptance and routing wherever
this stage refers to the spec or plan. The stage obligations remain unchanged.

Read `AGENTS.md`, `shared/process/SKILL.md` Stage 7 and the selected adapter.
Confirm the current candidate has a converged review, Backend QA and Frontend QA pass or
justified not-applicable verdicts, and no open P0/P1/P2. Stage 7 owns the result. For a TRIP item,
use `shared/process/integrate.md`; for a gstack item, use the gstack adapter's
Nadili-owned delivery mapping and only the compatible project steps from that release file.
Use project scripts for the ticket changelog, commit hook, exact affected integration gate,
fast-forward `HEAD:main`, bounded Linear update and guarded worktree cleanup.

Immediately after the terminal Linear transition, call Codex's current-task title operation. UAT
uses `UAT - <stable title>`; Done and Canceled use the stable human-readable title with no status
prefix. Do this before cleanup so terminal delivery cannot leave a stale `WIP`, `APR` or `BLK`
title. Report a title-operation failure once; it does not roll back successful integration.

Do not invoke TRIP or gstack `ship`, `land-and-deploy`, PR creation, ticket version stamping,
tagging or production deployment. Delivery follows the owner's earlier implementation start;
ask again only for an explicit plan-level confirmation or genuine owner-only blocker.
