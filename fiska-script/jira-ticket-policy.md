<!-- fiska:jira-ticket-policy:start -->
## Jira ticket required before development

Every Multica ticket needs a corresponding Jira ticket unless the user explicitly opts out.
This rule supersedes older optional-Jira and unconditional Multica-only branch guidance.
It applies to chat, quick-create, manual intake, imported work and child tickets. Capture may
precede Jira linking, but development must not begin while the link or user exemption is missing.

The ticket author or intake owner must search Jira for the same scope, reuse a verified match,
or create the missing ticket through an authorized write tool. Do this during intake, before
dispatching implementation. Record the primary Jira key and URL in the Multica description and
a Multica backlink in Jira. A related-ticket mention is not a verified match. Do not infer a Jira
project from a Multica prefix. Use established project context; ask when the destination or scope
is ambiguous. Include the outcome, scope, acceptance criteria and relevant context. Preserve
existing content, assignment and status unless the task authorizes their change.

Use Architect as the intake owner when available, otherwise the ticket's designated author or
squad coordinator. Use one designated intake owner for Jira creation. Other agents request that
owner through native delegation and await its result; do not race to create duplicates. Re-read both records before
writes. After a timeout or uncertain create, search for the Multica backlink and matching scope
before any retry; if the outcome remains uncertain, stop and report it. Children need their own
matching Jira ticket unless an existing ticket explicitly covers that child's scope; record the
mapping on each child. Do not blindly inherit a parent's link.

Before coding, editing implementation tests, or handing work to a coding worker, read the current
ticket and verify the Jira mapping, or cite an explicit user opt-out and its scope. Record an
exemption as `Jira: not required` with the user's message/comment reference. Agents cannot grant
this exemption. `/decisions auto` and delegation of decisions do not waive this requirement.
If Jira access, project selection, creation or confirmation is blocked, record the owner and exact
resume step, use the workflow's blocked state where available, and end the run without coding.
Read-only investigation can continue. An agent without Jira write permission delegates to the
intake owner; this policy does not expand tool permissions or authorize private-data disclosure.
Use the attached Jira write tool, typically `jira_triage__request` for scoped writes and
`jira__request` for searches and field metadata. Inspect available tool schemas before use.
Quick-create still follows its native single-create contract; mark unresolved Jira intake in the
created description for the next intake run and never treat capture as development approval.

Delivery must recheck the mapping or exemption and create the required PR. Use
`feature/<JIRA-KEY>-<description>`; only an explicit user exemption permits
`feature/<MULTICA-KEY>-<description>`. Preserve existing review and release authorization rules.
<!-- fiska:jira-ticket-policy:end -->
