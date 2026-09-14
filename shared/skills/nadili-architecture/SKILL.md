---
name: nadili-architecture
description: Resolve a Nadili item's technical design after the draft plan and before final plan approval, using the selected TRIP or gstack method where compatible.
---

# Nadili architecture

Read `AGENTS.md`, `shared/process/SKILL.md` Stage 2, the draft item plan and
its selected adapter. Review architecture boundaries, contracts, migrations, failure behavior
and test seams against canonical decisions. Use gstack `plan-eng-review` criteria for a gstack
item; invoke the whole vendor skill only if its required interactions and writes fit the
planning session. Use the TRIP plan-review method
for a TRIP item. A no-impact item needs a recorded no-change conclusion, not a new ADR.

Return an updated plan or focused ADR plus a clear verdict to `nadili-plan`. If the decision
changes product scope or acceptance, return to plan review and owner approval. Do not edit code
or describe planned architecture as implemented in `docs/ARCHI.md`.
