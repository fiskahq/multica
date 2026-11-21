# Factory improvement audit

Authorization: David requested applying missing improvements without creating tickets on
2026-11-20. He selected **more than 15 files** for the explicit approval threshold. This record
does not manufacture acceptance comments or create the per-change tickets proposed by the source
issue. Existing issue-specific decisions, especially FISK-172 K2(a), remain in force.

| Change | Owner | Implementation and remaining validation |
| --- | --- | --- |
| Observe external behavior first | Architect, planner; builder/QA execute | Ticket and planning skills, role instructions and intake step require assumptions, safe observation slices and evidence before dependent behavior. Real observations are still task-specific. |
| One behavior per PR | Planner; architecture reviewer checks | Independently verifiable slices, target 3-6 files; >15 hand-maintained files requires recorded David approval. Generated changes reported separately. |
| Invariants, facts, permutation tests | Planner/advisor; builder; code reviewer checks | Required for event/money work in the first candidate. All orders of the agreed finite fact set, separate replay/overlap cases, no single-writer shortcut. |
| Complete review round | Three reviewers; adversary aggregates | Early native back routes removed; same-SHA receipts flow through all three reviews before a combined return to Development. Sequential native scheduling retained. No live model run was started to claim behavioral validation. |
| Written blocking bar | Architect/planner; all reviewers enforce | Core regression, money, record-loss, security/compliance and acceptance failures block. Presentation/out-of-fact-list items are follow-ups unless they meet a core blocker. Explicit owner rules and repository requirements remain. |
| Late-gate tooling | FISK-190 builder; planner/QA for terminal coverage | Partial. Disposable tmpfs MySQL runner added; FISK-190 schema reuse remains active work. Terminal/emulator portability is not complete. Details below. |

## Tooling evidence and unresolved work

Read-only inspection of `fiskahq/merchant-services` remote-tracking branches found FauxPay and
`e2e/tests/card-present` on `release/release-29`, absent on `release/release-28-hotfix-3`.
FauxPay references `Fiska.Payments.Common` and baseline-specific contracts. This is a product/test
harness backport, not a Multica Docker setting or a safe copy of just a test directory.

The local stack documentation (`services/docs/agent-docker-environment.md`, Coverage boundaries)
states that no terminal server is provided and terminal transport targets `127.0.0.1:1`.
The Nuvei emulator covers card-not-present and ACH. Therefore neither Docker availability nor a
passing emulator health check proves the requested card-present end-to-end behavior works.

FISK-190/MS-1162 is already in Development for exclusive schema leases, safe resets and cleanup;
this change does not alter that active candidate or claim its acceptance criteria passed.
FISK-177 is the existing release-29 forward-port scope and remains parked pending its prerequisites.
No new ticket, dispatch, unpark, product backport or release-branch modification was made here.

Completing item 6 still requires selecting supported baselines, adapting the emulator's shared
contracts and local routing/fixtures to each baseline, then running the full MySQL suites and an
actual local card-present sale/follow-up flow on each exact candidate. The new policies expose this
as missing coverage with an owner and resume step, rather than accepting "MySQL not rerun" or
mistaking an unavailable terminal flow for a passing test. No claim is made that this product
integration has been completed or validated by the setup-script changes.
