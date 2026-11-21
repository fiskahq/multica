## Complete candidate review round

This policy replaces early rejection and any prerequisite that an earlier review must PASS before
the next review can inspect the candidate. Code, architecture and adversarial reviewers each review
the same immutable builder candidate SHA and base. Freeze that candidate until all three receipts
exist. A receipt records reviewed SHA, scope checked, all findings, evidence, coverage limits and
blocker/follow-up classification. Finish the complete relevant review; do not stop at the first
finding. Reuse valid receipts for that exact SHA, but never count an acknowledgement as a review.
Do not change code or ask the builder to start fixes during the round.

Use the ticket's written blocking bar. Default blockers are regressions in existing behavior,
wrong money, lost records, security/compliance defects and failed agreed acceptance criteria.
Presentation preferences and cases outside agreed facts are follow-ups unless they also meet a
core blocker. Mandatory repository checks and explicit owner decisions still apply. Give each
blocker a failure scenario, location, evidence and the criterion/invariant it violates. Collect
follow-ups in the verdict; do not create tickets without authorization. Do not block on style or
an unsupported severity label. Preserve explicit existing exemptions, including FISK-172 K2(a).

Code review first checks that event/money changes ship the agreed fact list, invariants and
permutation test in the first candidate, then completes the rest of its review even if missing.
Architecture review checks one behavior per PR and changed-file counts against the merge base:
target 3-6 files; more than 15 hand-maintained files needs David's recorded scope approval.
Generated files are reported separately. Check real concurrency protection, not a single-writer
assumption. Adversarial review checks unobserved external assumptions, permutations, replays and
overlapping writers, then consolidates all three receipts without dropping or weakening findings.

In Software Factory, native steps remain sequential. Code Review and Architecture Review post
their complete receipt and use Step done even when findings block acceptance; this means review
completed, not candidate approved. They do not use Needs changes or dispatch the builder.
Adversarial Review posts one deduplicated combined list with all three receipt links and exact SHA.
Only then: any blocker uses Needs changes to Development; no blockers uses Step done to QA.
Missing/unobtainable candidate or receipt is Blocked with owner and resume step, not approval.
Inspect all independently available scope before reporting a limitation. A changed or ambiguous
candidate invalidates the round; reconcile identity before proceeding, never combine different SHAs.

For squad-owned work, reviewers return receipts to the leader without moving the parent issue.
The leader obtains all three receipts on one SHA before requesting one builder rework. The builder
fixes the combined list, links each resolution and supplies a new SHA for a new complete round.
QA failures still require the existing full QA report plus planner fix plan before builder rework.
