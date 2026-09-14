---
name: nadili-plan
description: Plan a Nadili item after an explicit planning request; own its spec, Linear issue, worktree, plan review, architecture review and Ready for Development handoff. Do not implement.
---

# Nadili plan

Read `AGENTS.md`, `shared/process/SKILL.md` Stages 1–2, and only the selected
adapter in `adapters/`. For a new item select the active adapter
from `AGENTS.md`; for an existing item use the adapter pinned in its plan. Gstack planning
reviews supply focused criteria by default; a full vendor invocation is allowed only if all
mandatory questions, scope decisions and writes fit the Nadili contract.

New gstack plans follow `adapters/gstack/proportional-workflows.md`: bounded
discovery, then policy/route/reason before costly reviews. Include route in normal plan
approval, not another question. Approved legacy plans retain their existing role allocation.

Start only when the owner says to plan. Check `blockedBy` before creating a worktree or writing
the issue. For a new item, create the issue in `Specifying`, then its ID-named worktree;
freeze the spec and draft `docs/work/<ID>/plan.md` there. Run the plan
review under the recorded route's role matrix (legacy: separate report-only agents), then
`nadili-architecture` before the final owner plan decision. On approval, commit
and push the plan/spec/ADR, verify the plan names the committed spec path, and move this issue to
`Ready for Development`. Report the plan path, review
verdict, architecture decision and issue state. Stop before code until the owner starts it.
