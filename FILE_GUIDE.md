# Harness file guide

81 exported files and 3 synthetic test fixtures, each explained in one line.
Paths are relative to the portfolio root. `adapters/TRIP` and `adapters/gstack` separate
workflow policies; `shared` contains common mechanisms. Executable tools stay together
so tests can run, including the review runner used by the current gstack route.

Start with the [process](shared/process/SKILL.md), [gstack adapter](adapters/gstack/adapter.md)
and [orchestrator](adapters/gstack/orchestrator.md). TRIP remains for compatibility.
Exported file references are rewritten; references to omitted private product files stay unchanged.

## TRIP — legacy route and compatibility

| File | Purpose |
|---|---|
| [adapters/TRIP/process.md](adapters/TRIP/process.md) | Detailed legacy procedure for TRIP items. |
| [adapters/TRIP/adapter.md](adapters/TRIP/adapter.md) | Maps TRIP methods onto Nadili stages for compatibility. |
| [adapters/TRIP/implement.md](adapters/TRIP/implement.md) | Implementation and verification through the legacy TRIP route. |
| [adapters/TRIP/plan.md](adapters/TRIP/plan.md) | TRIP planning procedure, adapted for publication. |
| [adapters/TRIP/shims/TRIP-1-plan.SKILL.md](adapters/TRIP/shims/TRIP-1-plan.SKILL.md) | Redirects TRIP planning to plan.md. |
| [adapters/TRIP/shims/TRIP-2-implement.SKILL.md](adapters/TRIP/shims/TRIP-2-implement.SKILL.md) | Redirects TRIP implementation to implement.md. |
| [adapters/TRIP/shims/TRIP-3-release.SKILL.md](adapters/TRIP/shims/TRIP-3-release.SKILL.md) | Redirects TRIP integration to shared integrate.md. |
| [adapters/TRIP/shims/TRIP-review.SKILL.md](adapters/TRIP/shims/TRIP-review.SKILL.md) | Redirects TRIP review to review.md. |
| [adapters/TRIP/shims/TRIP-review.checklist.md](adapters/TRIP/shims/TRIP-review.checklist.md) | Redirects to the shared review checklist. |
| [adapters/TRIP/shims/TRIP-review.cr-template.md](adapters/TRIP/shims/TRIP-review.cr-template.md) | Redirects to the shared review report template. |
| [adapters/TRIP/shims/TRIP-test.SKILL.md](adapters/TRIP/shims/TRIP-test.SKILL.md) | Redirects TRIP testing to testing.md. |

## gstack — current route and Codex roles

| File | Purpose |
|---|---|
| [adapters/gstack/adapter.md](adapters/gstack/adapter.md) | Maps gstack methods onto Nadili stages. |
| [adapters/gstack/proportional-workflows.md](adapters/gstack/proportional-workflows.md) | Selects Small/Standard/High-risk routes and reviewer allocation. |
| [adapters/gstack/review-cycle.md](adapters/gstack/review-cycle.md) | Verifies corrections without restarting an unbounded review cycle. |
| [adapters/gstack/roles/architect.toml](adapters/gstack/roles/architect.toml) | Architect role: model, permissions and architecture review rules. |
| [adapters/gstack/roles/backend_qa.toml](adapters/gstack/roles/backend_qa.toml) | Backend tester role: model, permissions and independent scenarios. |
| [adapters/gstack/roles/executor.toml](adapters/gstack/roles/executor.toml) | Implementation worker: bounded work batches and available tools. |
| [adapters/gstack/roles/frontend_qa.toml](adapters/gstack/roles/frontend_qa.toml) | Browser tester role: model, permissions and journey verification. |
| [adapters/gstack/roles/reviewer.toml](adapters/gstack/roles/reviewer.toml) | Plan reviewer covering product, design and developer experience. |
| [adapters/gstack/roles/small_product_reviewer.toml](adapters/gstack/roles/small_product_reviewer.toml) | Focused product review for Small plans. |
| [adapters/gstack/config.toml](adapters/gstack/config.toml) | Codex sandbox, approval and agent concurrency settings. |
| [adapters/gstack/orchestrator.md](adapters/gstack/orchestrator.md) | Coordinates agents, budgets, code review and QA. |

## Shared stages, tools and checks

| File | Purpose |
|---|---|
| [shared/process/scripts/release-cleanup.sh](shared/process/scripts/release-cleanup.sh) | Cleans up a delivered worktree after item integration. |
| [shared/process/integrate.md](shared/process/integrate.md) | TRIP integration procedure with shared steps also used by gstack. |
| [shared/skills/nadili-architecture/SKILL.md](shared/skills/nadili-architecture/SKILL.md) | Reviews architecture before plan approval. |
| [shared/skills/nadili-backend-qa/SKILL.md](shared/skills/nadili-backend-qa/SKILL.md) | Independently verifies backend behavior in a running environment. |
| [shared/skills/nadili-deliver/SKILL.md](shared/skills/nadili-deliver/SKILL.md) | Integrates into main, updates item status and cleans up the worktree. |
| [shared/skills/nadili-discuss/SKILL.md](shared/skills/nadili-discuss/SKILL.md) | Explores ideas without starting delivery. |
| [shared/skills/nadili-frontend-qa/SKILL.md](shared/skills/nadili-frontend-qa/SKILL.md) | Independently verifies user journeys in a browser. |
| [shared/skills/nadili-hotfix/SKILL.md](shared/skills/nadili-hotfix/SKILL.md) | Completes a bounded fix without the full planning ceremony. |
| [shared/skills/nadili-hotfix/agents/openai.yaml](shared/skills/nadili-hotfix/agents/openai.yaml) | Hotfix display name, description and initial prompt in Codex. |
| [shared/skills/nadili-implement/SKILL.md](shared/skills/nadili-implement/SKILL.md) | Starts an approved plan and carries it through integration. |
| [shared/skills/nadili-plan/SKILL.md](shared/skills/nadili-plan/SKILL.md) | Prepares the specification, plan, issue and architecture review. |
| [shared/skills/nadili-review/SKILL.md](shared/skills/nadili-review/SKILL.md) | Reviews implementation before independent QA. |
| [shared/claude/settings.json](shared/claude/settings.json) | Connects the Git guard to Claude shell commands. |
| [shared/claude/skills/nadili-architecture/SKILL.md](shared/claude/skills/nadili-architecture/SKILL.md) | Redirects Claude to the matching skill in shared/skills/. |
| [shared/claude/skills/nadili-backend-qa/SKILL.md](shared/claude/skills/nadili-backend-qa/SKILL.md) | Redirects Claude to the matching skill in shared/skills/. |
| [shared/claude/skills/nadili-deliver/SKILL.md](shared/claude/skills/nadili-deliver/SKILL.md) | Redirects Claude to the matching skill in shared/skills/. |
| [shared/claude/skills/nadili-discuss/SKILL.md](shared/claude/skills/nadili-discuss/SKILL.md) | Redirects Claude to the matching skill in shared/skills/. |
| [shared/claude/skills/nadili-frontend-qa/SKILL.md](shared/claude/skills/nadili-frontend-qa/SKILL.md) | Redirects Claude to the matching skill in shared/skills/. |
| [shared/claude/skills/nadili-hotfix/SKILL.md](shared/claude/skills/nadili-hotfix/SKILL.md) | Redirects Claude to the matching skill in shared/skills/. |
| [shared/claude/skills/nadili-implement/SKILL.md](shared/claude/skills/nadili-implement/SKILL.md) | Redirects Claude to the matching skill in shared/skills/. |
| [shared/claude/skills/nadili-plan/SKILL.md](shared/claude/skills/nadili-plan/SKILL.md) | Redirects Claude to the matching skill in shared/skills/. |
| [shared/process/SKILL.md](shared/process/SKILL.md) | Primary contract for delivery stages, authority and required checks. |
| [shared/process/cr-template.md](shared/process/cr-template.md) | Shared code review report template. |
| [shared/process/gates.md](shared/process/gates.md) | Defines when checks run and when their evidence can be reused. |
| [shared/process/review-checklist.md](shared/process/review-checklist.md) | Review criteria, finding severity and approval requirements. |
| [shared/process/review.md](shared/process/review.md) | Manual fallback procedure for code review and audits. |
| [shared/process/testing.md](shared/process/testing.md) | Rules for testing, independent QA and verification evidence. |
| [shared/claude/skills/nadili-review/SKILL.md](shared/claude/skills/nadili-review/SKILL.md) | Redirects Claude to the matching skill in shared/skills/. |
| [shared/scripts/changed_areas.py](shared/scripts/changed_areas.py) | Classifies project areas affected by a change. |
| [shared/scripts/check_boundaries.py](shared/scripts/check_boundaries.py) | Checks forbidden dependencies between application layers. |
| [shared/scripts/check_hook_install.py](shared/scripts/check_hook_install.py) | Verifies and repairs the local pre-commit hook. |
| [shared/scripts/check_item_ready.py](shared/scripts/check_item_ready.py) | Checks that a worktree and plan or hotfix contract exist. |
| [shared/scripts/check_plan_interleaving.py](shared/scripts/check_plan_interleaving.py) | Checks that the item contract covers shared-state changes. |
| [shared/scripts/check_work_item_scope.py](shared/scripts/check_work_item_scope.py) | Prevents mixing unrelated work items in one commit. |
| [shared/scripts/codex_code_review.py](shared/scripts/codex_code_review.py) | Runs bounded independent code review and validates its result. |
| [shared/scripts/codex_code_review.schema.json](shared/scripts/codex_code_review.schema.json) | Defines the JSON format for review verdicts and findings. |
| [shared/scripts/codex_orchestration_budget.py](shared/scripts/codex_orchestration_budget.py) | Tracks agent and check attempts and enforces action limits. |
| [shared/scripts/codex_review_packet.py](shared/scripts/codex_review_packet.py) | Builds correction review context and deltas from Git snapshots. |
| [shared/scripts/dev-check.sh](shared/scripts/dev-check.sh) | Runs affected integration checks during development. |
| [shared/scripts/gate_evidence.py](shared/scripts/gate_evidence.py) | Stores and safely reuses Python check evidence. |
| [shared/scripts/gate_executor.py](shared/scripts/gate_executor.py) | Serializes heavy checks and supervises their processes. |
| [shared/scripts/gate_lanes.py](shared/scripts/gate_lanes.py) | Selects required checks from the changed files. |
| [shared/scripts/hooks/git_guard.py](shared/scripts/hooks/git_guard.py) | Blocks unsafe Git commands and writes to the wrong checkout from Claude. |
| [shared/scripts/hooks/pre-commit](shared/scripts/hooks/pre-commit) | Runs the mandatory fast check before Nadili commits. |
| [shared/scripts/hotfix_contract.py](shared/scripts/hotfix_contract.py) | Validates required fields and structure of hotfix contracts. |
| [shared/scripts/integration-check.sh](shared/scripts/integration-check.sh) | Runs required integration checks for a specified diff. |
| [shared/scripts/lib/gate-slots.sh](shared/scripts/lib/gate-slots.sh) | Shared shell helpers for check locks and resource allocation. |
| [shared/scripts/lint.sh](shared/scripts/lint.sh) | Runs backend static analysis. |
| [shared/scripts/pre-commit-fast.sh](shared/scripts/pre-commit-fast.sh) | Runs fast checks before commits. |
| [shared/scripts/pre-commit.sh](shared/scripts/pre-commit.sh) | Runs the full backend and both web client checks. |
| [shared/scripts/python-check.sh](shared/scripts/python-check.sh) | Runs the fixed Python checks with resource management. |
| [shared/scripts/review_telemetry.py](shared/scripts/review_telemetry.py) | Stores bounded review metrics and first-pass state. |
| [shared/scripts/test.sh](shared/scripts/test.sh) | Runs backend tests without external integrations. |
| [shared/scripts/web-check.sh](shared/scripts/web-check.sh) | Checks web clients; full mode also builds the applications. |
| [shared/scripts/worktree-new.sh](shared/scripts/worktree-new.sh) | Creates or resumes an item worktree with its dependencies. |
| [shared/scripts/worktree-preflight.sh](shared/scripts/worktree-preflight.sh) | Checks that a worktree can run the required gates. |
| [shared/tests/fixtures/codex_orchestration/legacy-budget-overruns-v1.json](shared/tests/fixtures/codex_orchestration/legacy-budget-overruns-v1.json) | Synthetic v1 fixture: review and fast-check budget overruns. |
| [shared/tests/fixtures/codex_orchestration/legacy-unknown-agent-history-v1.json](shared/tests/fixtures/codex_orchestration/legacy-unknown-agent-history-v1.json) | Synthetic v1 fixture: unknown agent context history. |
| [shared/tests/fixtures/codex_orchestration/legacy-review-import-v1.json](shared/tests/fixtures/codex_orchestration/legacy-review-import-v1.json) | Synthetic v1 fixture: ledger import and continued code review. |
| [shared/tests/unit/test_codex_code_review.py](shared/tests/unit/test_codex_code_review.py) | Tests review execution, failures, limits and result validation. |
| [shared/tests/unit/test_codex_orchestration_budget.py](shared/tests/unit/test_codex_orchestration_budget.py) | Tests ledger limits, recovery and concurrent writes. |
| [shared/tests/unit/test_gate_executor.py](shared/tests/unit/test_gate_executor.py) | Tests heavy-check locking and process termination. |

## Portfolio maintenance

These files belong to the portfolio, outside the exported Nadili layer.

| File | Purpose |
|---|---|
| [README.md](README.md) | Portfolio overview and visual introduction to the harness. |
| [docs/DESIGN.md](docs/DESIGN.md) | Visual division of responsibility between the owner and harness. |
| [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md) | Illustrative conversation between agents handling a fix. |
| [docs/assets/](docs/assets/) | SVG diagrams and Mermaid source for the overview. |
| [FILE_GUIDE.md](FILE_GUIDE.md) | This concise file guide. |
| [AGENTS.md](AGENTS.md) | Agent instructions for publication boundaries, synchronization and validation. |
| [export-manifest.json](export-manifest.json) | Exact export allowlist, destinations and publication substitutions. |
| [snapshot.lock.json](snapshot.lock.json) | Source revision, exported hashes and executable modes. |
| [.gitignore](.gitignore) | Hides files outside the explicit publication allowlist from Git. |
| [tools/snapshot.py](tools/snapshot.py) | Updates exports, checks publication contents and installs the hook. |
| [tests/test_snapshot.py](tests/test_snapshot.py) | Tests export protection against leaks and overwritten local edits. |
| [pytest.ini](pytest.ini) | Configures the selected portfolio test suites. |
