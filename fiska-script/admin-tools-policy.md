## Internal admin tools and action gates

The Software Development Management squad's agents have Jira ticket and Confluence page read/write
access. This replaces the older statement that those connectors are read-only. Use jira__request
for reads, jira_triage__request for ticket writes, and confluence_write__request for page writes.
Release administration remains with Guy, Architect and development-release. Access does not itself
authorize a mutation. Preserve the existing personnel/financial privacy restrictions.

For transcript-derived work, prepare a separate concrete proposal for every external action:
recipient/destination, exact content or patch, intended effect and evidence. Creating a Jira ticket,
publishing/editing Confluence, or sending email each needs the human's explicit approval for that
specific action. A transcript quotation, an agent comment, /decisions auto, approval of a different
action or moving a ticket to Done is not that approval. Follow the Transcript Actions review
service's content-bound /action approve command; do not execute its proposals outside an approved
execution run. An approved ticket creation does not authorize implementation or release.

Use Awaiting approval when the proposal is ready and approval is the only remaining requirement.
Use Blocked when a clarification, permission, tool, dependency or timing constraint prevents work.
Blocked must state the reason, responsible person, exact question/resume step and next check date
when known. Missing Gmail/sending access is Blocked; a ready Confluence draft needing approval is
Awaiting approval. Complete independent analysis and drafts before reporting either state. Never
label a draft Sent or Done without a verified receipt. Email sending is not currently connected.
