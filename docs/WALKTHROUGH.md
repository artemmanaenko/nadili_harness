---
document_profile: human-primary
canonicality: derived
owner: repository_owner
review_budget: 1 minute
---

# The fix that needed fixing

[Back to overview](../README.md)

You fill in a form. Click Save. The request fails. Everything you typed disappears.

*An imagined task, showing how the agents work together.*

**Me → Coordinator**
> Keep my text when saving fails. Let me try again.

The agents prepare and review a plan. I approve it, then tell them to start.

**Builder → Reviewer**
> The form keeps the input now. The focused tests pass.

**Reviewer → Builder**
> The text survives. But Save stays disabled after the error. How does the user try again?

**Builder → Reviewer**
> Fixed the button state. Added a check for failure followed by retry.

The reviewer gets both the new changes and the earlier finding. That question still needs
an answer before the work can move on.

**Browser tester → Coordinator**
> I made the save fail. The text stayed. I retried. It saved.

With review, applicable QA and final checks passed, the coordinator integrates the change.
If attempts run out before it gets there, it stops and brings the unfinished work back to me.

---

The harness carries the task between these roles, keeps track of what is still wrong, and
requires evidence before calling it ready. I set the direction; I do not have to relay every
review comment myself.

[Inspect the review checks](../shared/tests/unit/test_codex_code_review.py)
