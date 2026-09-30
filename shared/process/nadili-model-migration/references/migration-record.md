# Migration record template

Use this as a compact checklist in the consuming repository's existing work item. Replace each
instruction with a decision or evidence link; omit inapplicable fields with a reason. Reference the
skill for the method instead of copying it. Required local specs/plans remain authoritative.

## Scope and reuse

- Operation and observable outcome; current effective model -> candidate.
- Existing item/contract, owner authorization and active delivery/release workflow.
- Code/config/prompt/schema revisions; consumers and side effects; affected transports.
- Prior corpus, runner, labels and results reused; why still valid; invalidated evidence.
- Skill source path/revision or content hash; official documentation URLs and verification date.
- Non-goals and unresolved inputs; explicit phase reached (plan, qualify, activate).

## Frozen comparison

- Per-stage case map and oracle: compatibility, model quality, prompt diagnosis, effort probe,
  runtime QA and canary; why any case overlap is useful and where populations differ.
- Sampling frame/cutoff/seed, strata and denominators; representative vs challenge cohorts.
- Private snapshot/manifest fingerprints; per-case historical boundary where needed.
- Split/leakage grouping; development/holdout sizes and coverage rationale.
- Gold oracle and independent/blinded adjudication; ambiguous cases and missing slices.
- Blinded packet identity, source evidence, anchor/target alias mappings and separate unblinding key.
- A/B factor table; bounded optional prompt hypothesis and candidate-selection rule.
- Exact model/prompt/effort/schema/context caps/provider/region/transport per arm.
- Neighboring-effort assessment: supported settings, run/skip rationale, dev cases, fixed factors,
  trigger/timing, call budget, quality/cost/latency findings and limits on qualification.
- Quality thresholds, critical errors, uncertainty method, repeat protocol and no-go conditions.
- Cost/latency/reliability thresholds; why appropriate for this operation.

## Execution and evidence

- Measured or explicitly historical input/output usage, sample/date and pricing source; no double-counted reasoning.
- Expected base/conditional spend, pessimistic reservation and approved ceiling shown separately.
- Allowed attempts, spend, concurrency and candidate/review limits; staged pilot if justified, without weakening quality gates.
- Credential and metering path; evaluation attribution; durable attempt/budget authority.
- Product isolation, uncertain-call recovery and persisted-effect validation.
- Required local checks, compatibility sample, paired runs and independent QA boundaries.
- Result/artifact hashes; attempt failures, token/cost/latency observations and slice results.
- `$prompt-optimizer` invocation and contract/context audit for text-prompted work, or
  inapplicability/missing-dependency reason; failure clusters, candidate log and winner.
- Prompt-stage frozen case set, same-case candidate comparison, untouched holdout and any fresh
  baseline needed because its population differs from the model-only stage.
- Prompt-change hypothesis outcome; selected candidate frozen before holdout.
- Drift since evidence capture; required refresh or limits on the decision.

## Decision and activation

- Pass / fail / inconclusive, with exact accepted configuration and supported population.
- Blocking defects or evidence gaps; smallest bounded follow-up if any.
- Production authorization, canary population/sample/window and stop signals.
- Old/new job and data/index compatibility; rollout and rollback commands/configuration.
- Existing-data correction responsibility; activation outcome or pending release handoff.

Do not fill unknowns with assumed success. No-go is a completed qualification result when its
required evidence exists; missing evidence is incomplete/inconclusive, never a successful migration.


## Skill maintenance during this migration

- Observed omission, ambiguity or outdated claim; minimal synthetic reproducer and expected action.
- Generic correction and maintained source path; local policy distinguished from vendor guidance.
- Official OpenAI page/section, checked date and supported claim; any unresolved uncertainty.
- Skill/template hash before and after; affected scenario checks and outcomes.
- Prior evidence retained or invalidated, and why; publication/installation status where relevant.

If no gap was observed, record that finding rather than inventing a skill change. A repaired
instruction is not proof that the migrated production operation passes its separate quality gates.
