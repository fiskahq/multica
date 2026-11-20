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
