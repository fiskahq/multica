---
name: ticket-creation
description: Research, create and refine implementation-ready tickets with duplicate checks, bounded specialist consultation, evidence and testable acceptance criteria. Use for feature, bug, investigation and architecture intake in Multica.
---

# Ticket creation

Adapt depth to the request. A small fix needs a small ticket; a cross-system change may need an epic
and independently verifiable children. Follow the project's ticket conventions and the current
Multica run brief. Quick-create's single-create contract and field rules take precedence over this
ordinary refinement workflow.

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
