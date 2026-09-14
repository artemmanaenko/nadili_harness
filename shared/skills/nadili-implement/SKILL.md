---
name: nadili-implement
description: Start an approved Ready for Development Nadili item on the owner's explicit implementation instruction and carry it through code review, independent QA and ticket integration.
---

# Nadili implement

For an existing patch or concrete bounded fix without an approved plan, use `nadili-hotfix`
when the owner requests completion/integration. Do not manufacture a plan to enter this skill.

Read `AGENTS.md`, `shared/process/SKILL.md` Stages 3–7, the approved item plan
and its pinned adapter. Run the readiness check before moving Linear to `In Progress`.
For proportional items revalidate route before implementation and against the actual review
diff under `adapters/gstack/proportional-workflows.md`; never silently downgrade
risk or migrate an approved legacy plan.
Implement in the item worktree with the selected compatible vendor method. Keep code, tests,
contracts and canonical documentation aligned; do not treat a vendor completion tag as proof.

After implementation, continue through `nadili-review`, `nadili-backend-qa`,
`nadili-frontend-qa` and `nadili-deliver` without another routine owner prompt. An independent
tester performs each applicable QA route. On a defect, fix it, refresh affected review and
retest within the approved allowance. Stop for a real owner-only blocker, an explicit plan
decision or exhausted review/model allowance; recovery never expands it.
