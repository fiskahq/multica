---
name: ticket-creation
description: Research, create and refine implementation-ready tickets with duplicate checks, bounded specialist consultation, evidence and testable acceptance criteria. Use for feature, bug, investigation and architecture intake in Multica.
---

# Ticket creation

Adapt depth to the request. A small fix needs a small ticket; a cross-system change may need an epic
and independently verifiable children. Follow the project's ticket conventions and the current
Multica run brief. Quick-create's single-create contract and field rules take precedence over this
ordinary refinement workflow.

Every ticket requires a corresponding Jira ticket before development, unless the user explicitly
opts out. During intake, verify an existing scope match or create the missing Jira ticket, record
its primary key and URL in Multica, and add the Multica backlink in Jira. Follow the managed Jira
policy for duplicate checks, user exemptions and blocked intake. Quick-create may capture the
Multica ticket first; unresolved Jira intake must be completed before implementation is dispatched.

## Establish the facts

Read the user's request and existing issue/thread, project context, linked documentation, relevant
code and repository instructions. Identify the affected users, present behavior, desired outcome
and constraints. Distinguish observed facts, reasonable assumptions and unanswered questions.
Search existing Multica/Jira tickets and relevant PRs for equivalent behavior, not just matching
titles. Link prior decisions and related work. Prefer refining an existing authorized ticket to a
duplicate; do not close, merge or reassign other tickets without the user's requested scope.

For claims about an implementation or merge, cite the repository path and commit or the actual PR
state. Git ancestry alone cannot prove a squash-merged PR is still open. Use primary documentation
for external technical claims. Do not claim a test or reproduction ran when it did not.

Ask relevant available agents for bounded context when their expertise or connected sources are
needed. Supply the exact question and source context; request findings with citations, confidence
and remaining unknowns. Use native issue-comment mentions and reply routing, then end the run while
awaiting replies. Track pending questions in the issue so resumed runs reuse results instead of
redispatching. Integrate conflicting findings before treating a ticket as ready.

## Write a usable ticket

Use these sections where they add information, without empty boilerplate:

- **Outcome and reason:** who needs what, why it matters, and the concrete change in behavior.
- **Current behavior and evidence:** sources, affected surfaces and reproducible facts. For bugs,
  give reproduction steps, expected/actual results and environment details when known.
- **Scope:** included behavior and explicit boundaries that prevent a plausible misinterpretation.
- **Acceptance criteria:** numbered, observable checks. Include relevant failure, permission and
  boundary cases. Given/When/Then is useful when it makes a scenario clearer, not a required format.
- **Technical context:** existing contracts, data flows and conventions that constrain the work.
  Separate a proposed design from an approved decision. Use architecture-designer only when a
  meaningful design decision needs analysis; ordinary tickets do not require an ADR or diagram.
- **Verification:** how to demonstrate acceptance, necessary fixtures/environments and constraints.
  Match coverage to behavior and risk instead of prescribing tests that mirror implementation.
- **Dependencies and open decisions:** linked prerequisites, decision owner when known, and the
  exact information needed. Do not invent estimates, dates, priority or stakeholder approval.

Split work only when the parts have distinct outcomes or dependencies. Link children to their
parent and avoid parallel children editing the same contract without a clear dependency. Research
that blocks a solution belongs in a bounded investigation with a concrete question and deliverable;
do not label an unresolved design as implementation-ready.

## Check and persist

Check that scope and criteria agree, each material claim has evidence, and the ticket contains enough
context to implement without reading the entire conversation. For consequential or ambiguous work,
use a focused practices/QA review to challenge assumptions and testability. Incorporate the result.
If a material question remains, persist the useful draft with its blocker instead of inventing an
answer or waiting indefinitely. Do not repeatedly ask for confirmation when creation is authorized.

Read current issue content immediately before updating it. Preserve human edits and add findings
without overwriting unrelated changes. After a failed or uncertain create, search for the matching
issue before retrying to avoid duplicates. Use native description stdin/file options for multiline
content, verify the saved result, and return the issue link and unresolved decisions.

<!-- fiska:factory-policy:start -->
## Evidence and delivery contract

For new plans, record these sections on the originating ticket. Preserve explicit human scope
decisions and exemptions already recorded, including FISK-172's K2(a) finish line. Do not reopen
an in-flight candidate just to retrofit this template. A user prohibition on creating tickets
also prohibits automatic follow-up or slice tickets; describe the slices on the existing issue.

- **Assumptions not yet observed:** external behavior, supporting evidence, cheapest safe
  observation, owner and result. If a material assumption can be settled cheaply, the first
  deliverable is a small observation slice behind a disabled-by-default switch, using synthetic
  data and authorized access. Plan dependent behavior from its results. Planner/advisor specify
  the observation; builder/QA execute it. Do not substitute a mock for observing the external
  system. If observation cannot run, state the missing access/decision and resume step. Do not
  send dependent implementation to adversarial review with avoidable unobserved assumptions.
- **Behavior and slices:** one user-visible behavior per PR, independently buildable, verifiable
  and mergeable. Aim for the repository's 3-6-file increments; keep its other size guidance.
  More than 15 hand-maintained changed files requires David's explicit approval recorded on the
  ticket before implementation grows beyond that scope. Count product, tests, docs and config;
  report generated/lockfile changes separately with reasons. Exactly 15 does not trigger this
  approval. State each slice's outcome, base, dependency order and verification; independent
  slices should not wait on an unrelated rejected slice. Do not merge as part of planning.
- **Invariants and fact list:** for event-driven or money-moving behavior, enumerate the facts
  (events, amounts, identities and terminal states), valid ordering constraints and invariants.
  Include order independence where required, conserved approved/ledger amounts, replay safety
  and real overlapping-writer protection. A single-writer assumption is not concurrency safety.
  Specify a first-candidate regression test that enumerates every order of the agreed finite
  fact set and asserts the same invariants. Include duplicates and overlap tests separately.
  If exhaustive permutations are impractical, propose a smaller complete causal fact set or
  seek an explicit coverage decision; do not silently sample and call it exhaustive.
- **Blocking bar:** regression in existing behavior, wrong money, lost records, security or
  compliance defects, or a failed agreed acceptance criterion block. Presentation preferences
  and cases outside the agreed fact list are follow-ups unless they also meet that blocking bar.
  Severity labels alone do not change the bar. Preserve stricter explicit owner decisions and
  mandatory repository requirements. Record follow-ups without expanding the current slice.
- **Verification prerequisites:** exact baseline, full required MySQL suites and local end-to-end
  flow, required fixtures/emulator and commands. Check availability before implementation. Missing
  tooling is blocked coverage, not a pass or permission to silently omit tests. Use a disposable
  isolated test database; never accelerate tests by weakening durability of application databases.

Architect owns the ticket contract; development-planner owns intake and slicing; development-advisor
owns technical invariants, facts and file/symbol guidance. Builders implement the agreed tests.
<!-- fiska:factory-policy:end -->
