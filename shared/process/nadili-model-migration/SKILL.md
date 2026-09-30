---
name: nadili-model-migration
description: Qualify a model change for an existing AI operation using a fresh baseline, representative replay, task-specific quality gates, bounded prompt experiments and a rollout decision. Use when moving extraction, translation, classification, ranking, generation or embeddings to another model.
---

# Model migration

Turn an operation and a target model into a repeatable qualification. Reuse the operation's
contracts and previous evidence; write only the migration-specific decisions and results.
A model change is successful when the operation still satisfies its contract, not when a new
model accepts the request or produces plausible examples.

## Entry and process ownership

Start from the user's named operation and target model. Infer the current model, source files,
configuration, existing evaluation assets and prior authorization from the repository. Ask only
for missing decisions that cannot be recovered. Do not make the owner restate this checklist.

The consuming repository's instructions, active delivery process and existing approved scope
own planning, implementation, review, budgets and release. This skill supplies the migration
method; it does not invent another approval ceremony or waive a required one. A request to
plan or qualify is not permission to activate production. Continue already authorized steps.

In this harness, the delivery contract is [nadili-process](../SKILL.md). Its files are an exhibit,
not instructions to run product delivery inside this portfolio. This skill is a harness-owned
extension, not an imported claim that a production migration has already been implemented.

Reuse a prior migration record and runner when their boundaries still fit. Start from the
[record template](references/migration-record.md), storing the filled record in the consuming
repository's existing item/evaluation location. Link required spec/plan artifacts rather than
creating duplicate authorities. No fixed dataset size, score threshold, model pair, spend ceiling,
review count or prompt-candidate count is universal; choose them for this operation and risk.

## Official documentation basis

Last checked: 2026-09-30. Open the relevant current official page when maintaining this skill;
a dated check is not proof that an API, price or model capability is still current.

| Official OpenAI source | Applied guidance |
|---|---|
| [Build skills](https://learn.chatgpt.com/docs/build-skills) | Required name/description manifest, focused instructions, optional references/UI metadata, progressive disclosure and trigger testing. |
| [Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices) | Task-specific objectives and representative cases, explicit metrics, paired comparisons and human calibration of automated scoring. |
| [Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering) | Provide clear goals and relevant context, version prompts, pin snapshots where available, and evaluate behavioral changes. |
| [Model optimization](https://developers.openai.com/api/docs/guides/model-optimization) | Establish an evaluation baseline and iterate prompts against observed task failures. |
| [Prompt optimizer](https://developers.openai.com/api/docs/guides/prompt-optimizer) | Use annotated failures and specific critiques, then manually review and evaluate optimized prompts; its hosted dataset-backed surface is being deprecated. |
| [Testing Agent Skills Systematically with Evals](https://developers.openai.com/blog/eval-skills) | Verify actual agent actions and artifacts against a small set of outcome/process checks when revising a skill. |

The delivery permissions, spending limits, holdout policy, persistence protections and rollout gates
below are local engineering rules. They are not prescribed numeric standards or approvals from
OpenAI. Adapt them through the consuming repository's authority, never through an assumed vendor
endorsement. Use the evaluation methodology without assuming that a hosted evaluation product or
an example SDK command remains supported; check its current official documentation before adoption.

## 1. Trace the effective operation

Identify the actual caller, prompt layers, retrieved/contextual inputs, schema, parser,
postprocessing, validation, side effects and downstream consumers. Record:

- Effective model/provider/endpoint, revision or alias, region, transport, SDK, effort and other
  sampling parameters; trace environment defaults, per-operation overrides and saved job configs.
- Input/output/context limits, batching, truncation, timeouts, retries, caching and fallback.
- Model catalogs, UI selectors, pricing/accounting tables, deployment configuration and persisted
  settings that must agree. A catalog entry does not prove runtime or evaluation eligibility.
- Synchronous, streaming and asynchronous paths actually used; in-flight old-model results and
  data readers that must remain compatible.

Check current official model documentation and the real adapter's supported requests. A missing
parameter, unsupported schema, region or transport is a compatibility finding. Do not silently
change the prompt, effort, schema, context cap or fallback to make a model-only comparison work.
Record an inseparable API adaptation as a separate factor and qualify its combined effect.

For embeddings or other model-dependent stored representations, trace dimensions, distance
metric, indexes, caches and re-embedding/backfill. Compare retrieval on separate indexes; never
mix incompatible vector spaces or treat this as a text-prompt-only switch.

## 2. Freeze the evaluation contract before calls

Define the observable success unit and failures first. Select the smallest corpus that covers
material risks with useful confidence; reuse existing regression suites but inspect their oracle.
A parser pass, substring match or agreement with yesterday's model does not establish quality.

Freeze sampling frame, cutoff, seed, selection/strata, exclusion rules and reported population.
Build a case map for the **operation and decision stage**, not a universal test pack. Different
operations and stages need different inputs and oracles:

| Decision stage | Case selection |
|---|---|
| Compatibility | Small shape, schema, limit and transport probes; synthetic inputs may suffice. |
| Model quality | Representative real workload plus critical/challenge strata, labeled for this operation. |
| Prompt diagnosis | Development failures and successful controls that distinguish prompt, scope and context hypotheses. |
| Reasoning-effort probe | A bounded subset of relevant hard cases and controls; report its narrower population. |
| Runtime QA and canary | Stateful operator paths, failure/replay cases and then actual exposure distribution. |

Pair arms on the **same frozen cases within each comparison** so their delta is interpretable.
Do not force the compatibility, prompt, effort, runtime and canary stages to reuse one case set,
or carry Claim Resolution's Topics, denominators or labels into another operation. If a stage
changes the population or task boundary, label its result separately; refresh its own baseline
when a causal comparison needs one. A holdout is untouched for the decision it qualifies.
Cover typical inputs plus critical/rare cases: valid empty output, ambiguity, contradictory or
hostile content, long inputs, supported languages and state-changing actions where relevant.
Separate representative and deliberately difficult cohorts; disclose weighting and denominators.
Inventory missing coverage before asking for more sources, and request only the missing slices.

Use private, bounded snapshots of real inputs when available. Split development and held-out
sets by leakage groups such as source, conversation, document, customer and near-duplicate family.
Keep inputs, order, context, configuration and expected state transitions fixed across arms.
For stateful replay, reconstruct candidates, versions and evidence at each case's own pre-action
boundary *before retrieval*. Exclude later unrelated records, revisions and copied evidence too.
If historical state cannot be proved, label the case counterfactual or exclude it; do not claim
exact historical replay. Report the resulting coverage limit.

When retention has removed original inputs, a bounded fresh capture can define a separately labeled
current-state experiment. Freeze the selected context before capture and preserve provider publication
dates, fetch observations, source trust and structural metadata independently. Never substitute fetch
time for publication time. Reuse production extraction and ranking against the frozen projection;
record missing metadata and resulting limits before calls. Recheck leakage groups after retrieval: two
different Sources can share candidate targets or document lineage. Reduce the achieved split and report
uncertainty instead of crossing that boundary to meet a target sample count.

Build labels from evidence and the task contract, blinded to prior outputs and moderation outcomes.
Existing accepted outputs are diagnostic observations, not automatic gold. Expose only the prior
state needed to judge a target/action. Record independent adjudication, ambiguity and valid extras;
keep gold and held-out outputs out of prompts and tuning. Store raw inputs/results privately;
commit only safe methodology, hashes and synthetic regression cases allowed by the repository.

## 3. Establish baseline and isolate the model change

Reuse the production request builder, budget calculation, adapter, schema, parser and deterministic
validation. Freeze exact request/configuration fingerprints. Hold upstream filters/retrieval and
all other declared factors constant; separately test any production batching change implied by
the new model's limits. A larger context window is not permission to increase scope unnoticed.

Run in order: local contract checks, a bounded compatibility sample, then the paired comparison:

| Arm | Model | Prompt/configuration | Question |
|---|---|---|---|
| A | Current effective model | Current frozen contract | What does today's baseline do? |
| B | Candidate model | Same contract, declared API adaptations only | What changes with the model? |
| C, if justified | Selected model | A bounded candidate prompt | Does the prompt fix a measured failure? |

Interleave/randomize arms where practical; do not compare a cold candidate with a cached baseline.
Record actual resolved model, response validity, failures, token usage, cost and latency. Refresh
old evidence when prompt, corpus, retrieval, parser, model alias/revision, transport or runtime
semantics changed. Record unknown provider revisions; do not claim immutable model identity.

## 4. Score meaning, failures and uncertainty

Choose operation-specific metrics, units, critical errors, tolerances and minimum slice coverage
before inspecting candidate results. Examples, not mandatory metrics for every operation:

| Operation | Useful oracles |
|---|---|
| Extraction / resolution | Grounded precision, useful-fact recall, correct target/action, duplicates, complete procedures, valid suppression. |
| Translation / rewriting | Meaning, omissions/additions, names/numbers, locale and terminology; source-grounded adjudication. |
| Classification / ranking | Cost-sensitive false positives/negatives, ordering/relevance and critical-class recall. |
| Generation / summarization | Grounding, coverage, scope/modality and task completion; independent semantic judgment. |
| Embeddings / retrieval | Relevant-result recall/rank, missing evidence, index compatibility and downstream outcomes. |

Use deterministic checks for mechanical constraints and blinded semantic review for meaning.
Blinded packets retain source evidence, task constraints and complete anchor/target alias mappings.
Remove model/configuration, cost and timing labels without losing the identity mapping needed to
judge an action. Freeze the packet identity and retain an audited unblinding key separately.
A model must not be the sole judge of its own changes. Calibrate any automated judge against
independent evidence and include its calls in the budget. Score failed/missing/malformed outputs
explicitly; do not drop them, label empty precision perfect, or turn a valid empty answer into an error.

Report paired deltas and uncertainty appropriate to the unit, clustering dependent cases rather
than pretending every output is independent. Predeclare repetitions on unstable/high-impact cases
and score critical failures across all repeats. Record executed and skipped repetitions with their
actual denominators; a partial repeat schedule cannot establish the planned stability gate.
Separate overall and slice results, fresh-record
and state-update behavior. A small corpus can support a limited decision, not an unsupported
claim of equivalence. Return pass, fail or inconclusive; never relax thresholds after seeing results.

## 5. Improve the prompt only when evidence warrants it

For every text-prompted migration, **invoke the local `$prompt-optimizer` skill** to capture
the prompt contract and inventory its external context before deciding that the prompt is sound
or proposing an edit. This is the agent skill, not OpenAI's hosted Prompt optimizer product.
Use its failure clustering, concrete edit criticisms, candidate comparison, optimization log
and holdout checks when a prompt experiment is justified. Its general loop supplies methods;
this migration's operation-specific case map, oracle, budget and delivery authority still govern.
If the skill is absent, locate it in the consuming repository or installed skill catalog and
record the missing dependency; continue independent model-only work, but do not claim a
prompt-optimized result or run prompt candidates while it is unavailable. For an operation
without a text prompt, record why this step is inapplicable.

Use development errors to state one testable hypothesis at a time. Distinguish model capability,
retrieval/input loss, parser limits and prompt ambiguity before editing. Keep task/domain data
in its proper layer; do not hardcode a failing customer's or dataset's example into universal rules.

Audit the rendered request against the oracle before attributing omissions to model capability:
system rules, desired output, actor/scope exclusions, evidence, candidate context and truncation.
A source-supported fact is not automatically required by the task. Preserve the original gold;
record disputed scope or bundled-detail requirements separately and freeze any sensitivity subset
before new outputs, applying it symmetrically to both models. Do not reward a changed task as an
improvement on the old one.

Audit whether the prompt and oracle optimize the same product objective. For example, selecting
material for one concise answer differs from retaining independently useful knowledge for future
questions. A counterfactual deletion test can enforce the former unintentionally. Treat changing
that objective as a declared semantic contract experiment; align every dependent admission,
exclusion and output rule, and preserve unrelated safety and grounding constraints. Explicit goals
and clear constraints follow official [reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning-best-practices)
(checked 2026-09-29); this particular diagnosis and experiment design are local methodology.

For compound gold units, report full, partial and absent meaning separately. Explain which missing
conditions change applicability or action; partial coverage does not excuse material errors. Do not
interpret a failed all-or-nothing unit as zero useful knowledge. Preserve frozen scores and
denominators; any alternative oracle is separately versioned and evaluated symmetrically.

Separate universal prompt rules from task-specific scope configuration. Inclusions for particular
subjects or actors belong in the selected task/Topic configuration, not in the generic application
prompt. When both need changes, freeze a prompt-only arm and then the same prompt with the scoped
configuration change; report each arm and repetition separately. A changed task boundary is not a
model-only improvement. Honor an owner exclusion of additional self-check or diagnostic steps.

Use one-factor development experiments to distinguish prompt wording, desired-output clarity and
context effects; compare both models where that distinguishes a shared contract problem from a
migration regression. Save the exact effective request and variant hash. Removing comparison targets
changes a resolution task: factual coverage may remain comparable, original action/target accuracy
does not. Report that limit and require repeat evidence before making a causal claim.

For a prompt-structure refactor, freeze the original and map every rule, exception and runtime-added
instruction to the candidate. Prefer an initial layout-only candidate when preserving tuned behavior
is critical; declare moved text, reference rewrites, metadata removal and new navigation separately.
Check list/negation scope and internal links, not only string presence. Keep semantic compression,
role/language changes and effort changes as separate experiments. Textual preservation is not proof
of behavioral equivalence; retain adversarial and multi-item cases and repeat unstable examples.
Markdown is supported prompt structure; XML or rule IDs are hypotheses, not guaranteed improvements.

When a long instruction stack may suppress useful output, distinguish an audience-framing change,
explicit admission criteria and a compact diagnostic control. Freeze each arm before calls; hold
task data, schema and runtime fixed. Preserve hard safety/evidence/scope constraints, map removed
or compressed rules, and disclose unresolved semantic differences. A short control with multiple
changes is a bundled intervention, not proof that length alone caused the result. Keep its outputs
out of production qualification until the complete contract is restored and independently checked.
Report omitted propositions, missing conditions/components and factual distortions separately;
these are different failure modes even when one compound recall metric penalizes all three.
This bounded comparison is local experimental policy, not an OpenAI-prescribed test matrix.

When omissions remain unexplained, consider a bounded evaluation-only exclusion report: omitted
meaning or condition, evidence reference, reason category and applicable rule. Request concise
observable decision summaries, never private deliberation or a reasoning transcript. The official
[reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning-best-practices) recommends
simple, direct instructions without chain-of-thought prompts (checked 2026-09-29). This diagnostic
method and the safeguards below are local experimental policy, not a vendor quality guarantee.

- Treat the added instructions and output schema as a new intervention. Freeze them together;
  validate the envelope separately and pass the unchanged task-result projection to the existing
  parser. Never write diagnostic text as product knowledge or silently discard malformed reports.
- Score task outputs without the explanations first, freeze those scores, then audit explanations
  against evidence and rules. Self-reported reasons are hypotheses, not trustworthy introspection.
- Distinguish a valid exclusion, an invented requirement, a disputed scope boundary, an incomplete
  explanation and an unaccounted omission. A broad topic-level reason does not account for every
  missing condition. Existing comparison targets must not become a new excuse to suppress required
  corroboration or another task action.
- If a generic exclusion report leaves known development omissions unexplained, use a separately
  labeled, gold-aware follow-up: freeze the original request and answer, then provide the relevant
  expected meanings/conditions and ask about each missing unit explicitly. Distinguish a panel
  denominator from case-local expectations. Permit disagreement with the checklist and uncertainty
  about the prior decision; do not demand a fabricated rationale. Keep original scores immutable
  and exclude this leaked-label response from extraction quality or held-out qualification. Its
  explanations suggest prompt experiments; they do not establish the cause of the earlier answer.
- Bound diagnostic length and record truncation. Check evidence references and verbatim quotes when
  requested; distinguish quote fidelity from semantic support. Valid JSON does not prove truthful
  explanations: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
  still permits content mistakes (checked 2026-09-29).
- Compare repeated task quality, critical errors, actual reasoning/output tokens, cost and latency
  against the unchanged prompt. The same effort setting need not consume the same reasoning work;
  a diagnostic report is not passive observation and may change retention or runtime substantially.

In the `$prompt-optimizer` loop, compare candidate prompts on the same frozen **prompt-stage**
development cases, with task-local scope/configuration unchanged. Select a winner before opening
that operation's untouched holdout. Do not require the same cases used for compatibility or
model-only qualification; record overlap, changed denominators and any fresh paired baseline.

Declare candidate count, targeted metric/unit/denominator, meaningful gain and stop rule before
candidate calls. Compare C against B, preserving the baseline. Choose and freeze the candidate
on development evidence before opening the holdout. A failed holdout means no-go or a separately
authorized experiment with fresh held-out evidence; no tuning on the same holdout or selecting
an unplanned fallback after seeing its results. Accept the model-only change if prompt edits
provide no material benefit. An effort/provider sweep is another declared factor, not a hidden fix.

## Check neighboring reasoning settings

At each model migration, explicitly assess whether a bounded reasoning-effort comparison is useful;
record supported settings and either a small experiment or a concrete reason it is inapplicable.
Models change: the old effort may be unnecessary, insufficient or worse on the new model. Do not
assume quality improves monotonically with effort or that the same label implies equal work across
models. Verify the current model/API support in the official model page and
[Reasoning models](https://developers.openai.com/api/docs/guides/reasoning) (checked 2026-09-29).
This experiment policy is local methodology, not an OpenAI-prescribed sweep or quality guarantee.

Keep the main model-only A/B effort fixed. If warranted, use the nearest supported lower/higher
settings on a small, predeclared set of development cases covering relevant failures and successful
controls. Hold model, prompt, inputs, output cap and transport fixed; reuse valid same-case baseline
results. Compare semantic quality, critical errors, completion, billed tokens/cost and latency.
Predeclare call count, trigger/timing and stop rule within the existing cumulative budget; do not
quietly add a factorial prompt-by-effort sweep or spend the full budget on optional diagnostics.

The check may run early to diagnose failures or near the final report. After holdout opening it
remains descriptive: no tuning, selecting a fallback or changing a qualification verdict on that
same holdout. A promising setting needs its own frozen-configuration qualification with adequate
untouched evidence under the consuming project's process. A few examples do not prove improvement.
Record better, worse, equivalent or inconclusive outcomes without forcing a configuration change.

## 6. Bound execution and protect state

Reuse the repository's existing evaluation runner, credential access and budget/accounting hooks.
Estimate expected spend from existing usage and offline request sizes before proposing a dollar
ceiling. Label historical proxies and unknown candidate reasoning lengths. Separate expected spend,
pessimistic reservation and the owner-approved limit; a model's context/output maximum is not a
representative workload. Count billed reasoning once, as defined by the provider. If uncertainty is
material, propose a small paired cost/compatibility pilot inside the corpus and approved budget,
then update the remaining estimate. A pilot does not replace held-out quality evidence. Avoid
arbitrary large safety multipliers or reserving every optional experiment as the default run.
Inventory call counts, maximum token envelopes, pricing tiers/regions, retries, optional arms,
repetitions and judge calls before spending. Reserve within the approved call/spend/concurrency
ceilings; if the complete proposed campaign cannot fit, revise scope before calls or request a
specific amendment. Do not truncate representative data or silently enlarge budgets.

Evaluate without writes to live product data. Test persistence separately in disposable state,
resetting both arms to the same starting point. Distinguish evaluation spend from production
workload and source-history attribution. Record every physical billable attempt, including SDK
retries; disable unobservable retry/fallback paths. Never export secrets or raw content into logs.

Where concurrent dispatch or resumption is supported, name one durable admission-budget authority
and its atomic reservation/recovery protocol. An accounting row with zero reserved dollars is not
itself a dollar-budget reservation. Preserve the established metering order, including charging
usage before parsing when the gateway does so. Metering completion and result-artifact storage
are separate durable facts. An uncertain call or billed call with missing output remains charged
and incomplete; never reissue it automatically. Test the relevant crash/concurrency boundaries.
Reuse existing safe mechanisms; do not build a new ledger or platform when they already suffice.

If the approved budget policy settles completed reservations to measured spend, release only the
unused portion backed by matching durable metering and stored-result facts. Unknown or incomplete
attempts retain their full reservation. Keep historical maxima, actual spend and admission exposure
separate; an explicit policy amendment preserves the original ledger, limits and all attempts.
Never reset a campaign to regain budget, or mistake accumulated historical maxima for money spent.

## 7. Qualify runtime behavior and decide

Exercise the real operator/job/API boundary with the candidate, not only its evaluation wrapper.
Cover applicable failure, tenancy, replay/idempotency, target updates, cancellation, partial output,
old in-flight jobs and all enabled transports. Use controlled responses for faults and bounded
real calls for provider compatibility. Route independent review and backend/frontend QA according
to changed behavior and repository policy; a selector change may require browser QA.

Publish a concise evidence-backed pass/fail/inconclusive decision for the exact model, prompt,
parameters and covered population. Attach manifests and complete check results. Missing required
coverage or an unresolved critical regression blocks activation; saving money cannot cancel it.

For a passing candidate, prepare concrete activation/rollback steps under the repository's release
process. Reuse existing overrides/flags, define canary scope, exposure/sample minimum, observation
window and stop signals, then expand only with qualifying evidence and authorization. Specify how
old and new jobs coexist. For model-dependent stored data, qualify readers and rebuild/rollback
before switching traffic. Reverting model/configuration stops future writes; it does not undo
already changed data. Identify affected records and use the existing correction/recovery path.

Leave a reusable migration record: next time fill the changed operation/model/configuration,
reuse still-valid corpus/runner/oracles, and rerun only invalidated evidence plus required gates.


## Maintain the skill while using it

When actual use reveals an omitted step, ambiguity, stale reference or incorrect assumption,
repair the maintained skill in the same authorized work instead of leaving only a chat workaround.
Capture the observed failure and intended behavior in the migration record without private inputs.
Change the smallest reusable instruction or linked template; keep case-specific rules, model IDs,
thresholds and data in the migration record. Do not copy private prompts, transcripts or real cases
into a public skill.

Check the relevant official OpenAI documentation above and model/API documentation for technical
claims. Record source URLs, check date and any remaining uncertainty; distinguish sourced facts
from local policy. If documentation does not establish a behavior, mark it unverified and qualify
it with a bounded compatibility check instead of inventing support.

Validate manifest/metadata/links and exercise the affected behavior with a synthetic scenario and
observable must-pass checks. Recheck relevant previous scenarios, including a request that should
not trigger migration and a plan-only request. A larger behavioral change may warrant an independent
forward-test under the existing budget; this is not permission for unbounded new review/eval loops.
Keep the correction, rationale, skill revision/hash and validation result in the migration record.
Refresh only evidence invalidated by the change, while retaining required repository gates.

Write only to the authorized maintained source. If a worker is confined to an item worktree,
hand the sanitized correction to the coordinator for the separate harness checkout. Follow that
repository's rules for imported files, validation and publication. Routine documented gap repairs
within authorized scope need no new scope proposal. Changing product semantics, acceptance gates,
authority or budgets requires the existing decision process; do not hide it as skill maintenance.
