---
name: nadili-backend-qa
description: Independently test backend behavior of a Nadili item in an isolated runtime.
---

Read and follow the project-owned .agents/skills/nadili-backend-qa/SKILL.md.

In Claude Code, dispatch every fresh-context role this contract names (reviewer, architect,
explorer, executor, tester) through `shared/claude/skills/codex-role/SKILL.md`, never as a Claude subagent.
