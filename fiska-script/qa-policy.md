## Complete QA and planned fixes

Run the complete test scope required by the repository and ticket against the exact candidate
SHA, including relevant unit, integration, end-to-end and acceptance checks. Inventory the
required suites before running them. Disable fail-fast/bail options using the runner's documented
flags. Run independent suites even after another fails; capture each command's exit status and
report, rather than using an `&&` chain or suppressing errors. Continue within a failed suite
where the runner supports it. Do not weaken assertions, edit implementation, or omit known
failures to obtain a green result.

If a prerequisite fails, mark dependent tests blocked/not run with the precise cause, then run
all independent checks. Bound hung tests with reasonable timeouts and report incomplete coverage.
Stop only work that cannot run safely or lacks required authorization. Distinguish product
failures, environment failures, skipped tests and tests not run. A failed prerequisite is not a
test pass. Do not claim the complete suite ran when coverage is missing.

Post one consolidated report on the originating issue, with counts and a per-suite result table,
every failure (deduplicated by cause without dropping affected cases), reproduction commands,
expected/actual behavior, relevant logs or durable attachments, environment/dependency versions,
repository, target/source branches, base and exact tested SHA, Jira link, acceptance criteria,
prior review receipts and any limits. Exclude secrets and production data from evidence.

If fixes are needed, delegate a bounded fix-planning consultation to {{PLANNER}} on the same issue
using a native comment mention, with --parent for a comment-triggered run. Include the full report
and evidence links, not just the first failure. Check for an existing request/plan for this SHA
before dispatching once, then end the run and await the reply. Keep the issue in QA while planning;
do not dispatch the builder or advance status in the same turn. This consultation is an explicit
exception to the generic worker no-mention rule and is not a new workflow or duplicate ticket.

The planner returns a concrete fix plan tied to that SHA. QA checks that all findings are covered,
then posts the report and plan together and uses the native Needs changes handoff to Development
once. For squad-owned work, return this packet to the leader, who delegates the fixes to the
builder. Acknowledgements are not plans. Missing access or an unresolved decision remains Blocked
with owner and resume step; finish independent tests and planning first. On a new candidate,
repeat the required reviews and full QA scope, including the earlier failures and regressions.
This policy replaces older instructions to return work immediately after the first QA failure.
