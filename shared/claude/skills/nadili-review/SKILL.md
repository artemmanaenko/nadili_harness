---
name: nadili-review
description: Review a Nadili item against its spec and plan and resolve P0/P1/P2 findings before QA.
---

Read and follow the project-owned .agents/skills/nadili-review/SKILL.md.

In Claude Code, Stage 4 runs `shared/scripts/codex_code_review.py` exactly as that contract specifies for
Codex; it is already a Codex CLI run. Never spawn a Claude reviewer subagent for it.
