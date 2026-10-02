# Regression result contract

Write the filled report in the consumer's private results location. Use its required metadata.
Record a unique run ID; keep previous reports immutable. Use safe IDs and sanitized evidence,
not real mailbox addresses, raw messages, authentication values or signed URLs.

## Identity and scope

- Requested target and exact selection: named suite or full.
- Consumer revision, protocol/registry revision and dirty diff; actual running build identities.
- Environment, fixture snapshot, browser/viewport/locale matrix and opaque test-account labels.
- Authorization, resources/budget and safe cleanup scope; coordinator and any independent testers.
- Expanded case and required-variant inventory, dependency order and required evidence layers.
- Product coverage inventory, explicit gaps/exclusions and completeness verdict.

## Case evidence

| Suite / case / variant | Preconditions | Action | Expected | Observed | Evidence layer / pointer | Verdict |
|---|---|---|---|---|---|---|
| sample-edit / EDIT-01 / primary | Isolated sample fixture | Save an edit | Saved value survives reload | Fill only after observation | Browser + persisted state | NOT RUN |

Include every selected case and required variant. PASS requires observed expected behavior at
the prescribed layer. FAIL means reproduced deviation. BLOCKED names the unmet prerequisite.
NOT RUN means unexecuted, with a reason. An expected demonstration of an existing defect stays
FAIL for the product even if a test runner exits successfully.

For asynchronous or negative outcomes record the trigger, durable state, processing completion,
observation window and destination checks. Distinguish not-enqueued, pending, suppressed, failed,
transport-accepted and externally observed delivery where applicable. Avoid exactly-once claims
that the transport evidence cannot support.

## Aggregate verdict

Show counts as `PASS + FAIL + BLOCKED + NOT RUN = selected required cases/variants`.
Do not omit blocked variants or change the denominator after observing results. Each required
evidence layer must be satisfied before its case passes; a component suite is not an E2E pass.

State reproduced failures separately from environment and coverage gaps. Full PASS requires all
active suites and required variants to pass, plus a current complete product-area inventory.
Otherwise use FAIL or BLOCKED/INCOMPLETE with exact remaining work. Predeclared justified
exclusions are visible; they are not a mechanism to drop required product coverage.

## Handoff and resumption

List defects with stable case IDs, trigger, impact, expected/observed behavior and safe evidence.
Record retained test objects, pending asynchronous effects and cleanup performed. Do not erase
user data or claim that future queued work has completed. Name invalidated evidence after a
fix/build/configuration/fixture change and rerun affected cases; preserve the earlier failure.

The public skill repository receives only synthetic evaluation examples or an independently
reviewed generic instruction correction, never this filled consumer report.
