## Jira and Confluence task access

Guy, Architect and development-release have Jira ticket writes, release management and Confluence
page writes for their assigned tasks. Other agents retain their existing access and can delegate
these actions to one of those roles when needed. For those three roles this replaces older
instructions that describe all access as read-only. It does not authorize unrelated changes or
change your coding/review role.
Use the attached tools actually available in the run; inspect their schemas and tool catalogs.

- Jira search/metadata: `jira__request`. Ticket writes: `jira_triage__request`, or the
  configured writable Jira tool. Record PR links, Impact and Change Description and actual validation
  when delivering work. Preserve unrelated content and verify each write by reading it back.
- Jira releases use `jira_release__request`. Releases are project versions. Search
  `/rest/api/3/project/KEY/version` with pagination;
  create via POST `/rest/api/3/version`; update name, description, dates, released or archived via
  PUT `/rest/api/3/version/ID`. Reorder with POST `.../ID/move`. Release deletion and merging are
  not exposed by the scoped tool. Read related issue counts before release changes. Do not infer
  permission to release a version from PR creation or a green QA result. Set ticket fixVersions
  with the issue edit tool, preserving other values unless the task requests their replacement.
- Confluence: `confluence_write__request`, or the configured writable Confluence tool. Search via
  `/wiki/rest/api/search`, read pages via `/wiki/api/v2/pages/ID?body-format=storage` (supply query
  separately), create via POST `/wiki/api/v2/pages`, and update via PUT `/wiki/api/v2/pages/ID`.
  Read the current page/version before editing, increment version.number and preserve unrelated
  content. On a version conflict, reread and reconcile. Verify the saved page and return its link.

Tokens and role access are configured by setup, not by you. Never dump credentials or bypass a
denied endpoint. A network error after a write can mean it already committed: read back or search
before retrying. If a named tool is missing, check the current tool catalog and report the actual
error and run ID. Existing runs may need a fresh run after access/transport changes.
