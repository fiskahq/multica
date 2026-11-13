# Factory MCP relay

One Docker relay with separate authenticated MCP endpoints for Multica agents. The service owns
provider credentials, connects to configured MCP servers, wraps fixed commands, and optionally
fetches Gmail and reads Google Drive.
This directory is a standalone Node package within `fiska-script/`.

| Integration            | Tools and behavior                                                                                               |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Jira                   | `jira__request`: REST v3 search, tickets, fields, comments, transitions and updates; scoped API tokens supported |
| GitHub                 | `github__request`: repositories, issues, pull requests, checks and Actions through REST                          |
| Confluence             | Configured `confluence__request`: spaces, pages, and CQL search through REST                                     |
| Cloudflare             | Configured per-zone REST tools for DNS records, settings, and analytics                                          |
| Mermig                 | Fixed `command__mermig_plan_dev` and `command__mermig_check` examples using the factory's existing Mermig image  |
| Other MCP services     | HTTP, legacy SSE, or stdio upstreams; discovered tools become `NAME__TOOL`                                       |
| Other factory commands | Operator-defined executable and argument lists; optional validated JSON on stdin                                 |
| Gmail                  | Read-only API polling into the persistent inbox; `gmail__sync` also imports on demand                            |
| Mail                   | `mail__list`, `mail__get`, `mail__attachment` for fetched messages and attachments                               |
| Google Drive           | `gdrive__list`, `gdrive__get`, `gdrive__download`, `gdrive__export`, including shared drives                     |
| Diagnostics            | `relay__status`, `relay__refresh`                                                                                |

## Start

Requires Docker Compose and Node 24 or newer for the setup command. From this directory:

```bash
npm run setup
# Complete Google login below and edit config.json to add other integrations.
../setup-relay.sh
curl --fail http://127.0.0.1:8090/healthz
../sandbox.sh logs --tail=50 mcp-relay
```

Setup creates `config.json`, `.env`, `data/`, and a random `.secrets/relay-token`. It preserves
existing files and sets the container UID/GID to the current user's IDs. The default configuration
fetches Gmail with a 60-second polling interval and enables Drive tools. Complete Google login below
to supply the required credentials. Until then, the service stays available and `relay__status`
reports failed Gmail sync attempts. `.env`, secrets, data, and local configuration are ignored.

The combined administrative MCP URL is `http://127.0.0.1:8090/mcp`, authenticated with
`Authorization: Bearer <contents of .secrets/relay-token>`. Scoped endpoints use their own tokens
as described below. Only `/healthz` is unauthenticated; it reports process health, not provider
health. Use `relay__status` on the administrative endpoint for upstream and Gmail status.
The transport is stateless Streamable HTTP. Authenticated GET and DELETE return 405 by design.

The MCP port binds to loopback by default. Mail intake uses outbound HTTPS requests to Gmail;
no SMTP listener, inbound mail port, mail domain, or Gmail forwarding rule is required.
Stop only this service with `../sandbox.sh stop mcp-relay`; fetched messages remain in `data/`.
The parent setup owns the Compose lifecycle; see [managed deployment instructions](../README.md#managed-custom-mcp-relay).

## Connect Multica

The local deployment has seven scoped endpoints. Each is registered as a separate shared server
in the Fiska workspace's **Settings > MCP servers** library:

| Multica server                             | Endpoint path                           | Exposed tool                                |
| ------------------------------------------ | --------------------------------------- | ------------------------------------------- |
| `factory-jira`                             | `/mcp/jira`                             | `jira__request`                             |
| `factory-jira-triage`                      | `/mcp/jira-triage`                      | `jira_triage__request`                      |
| `factory-confluence`                       | `/mcp/confluence`                       | `confluence__request`                       |
| `factory-cloudflare-fiska-com`             | `/mcp/cloudflare/fiska.com`             | `cloudflare_fiska_com__request`             |
| `factory-cloudflare-fiska-net`             | `/mcp/cloudflare/fiska.net`             | `cloudflare_fiska_net__request`             |
| `factory-cloudflare-fiska-io`              | `/mcp/cloudflare/fiska.io`              | `cloudflare_fiska_io__request`              |
| `factory-cloudflare-integratedcommerce-io` | `/mcp/cloudflare/integratedcommerce.io` | `cloudflare_integratedcommerce_io__request` |

The base URL is `http://mcp-relay:8080` for Dockerized Multica and
`http://127.0.0.1:8090` for host clients. From an agent's MCP tab,
use **Add from workspace** to assign only the services it needs. Registration does not assign
servers to agents or change existing workspace entries. GitHub and Mermig have no registered
endpoint until their underlying integration is configured and verified. Slack and Google are
not exposed by these endpoints.

The Fiska agent assignments are defined in [examples/agent-mcp.json](examples/agent-mcp.json):

- Jira and Confluence: the nine Software Development roles, Mika, the management leader, partner
  liaison, R&D planner and five Jira triage roles. People-notes gets Jira only; finance gets
  Confluence only. Each server is required by 19 roles in the policy. Archived agents can retain
  historical binding records.
- The four Cloudflare zones: `development-researcher`, for read-only infrastructure investigation.
- Factory database/migration/deployment roles were retired because their required brokers and
  guarded runners are not available in Multica.

Both active squads are reconciled through [the native setup](../README.md).
Management skill sources remain in `../management-multica-2026-10-31`. Both manifests verify that
their MCP declarations agree with this assignment policy before making changes.

The existing integrations remain read-only. The separate `factory-jira-triage` endpoint permits
individual issue creates, edits, comments, links, assignment and transitions, and rejects deletes,
bulk writes, archive, notifications and administration. Only `jira-triage-curator` and
`jira-triage-requirements` receive it. All five Jira triage roles also get Jira and Confluence
read access. See [the relay configuration](../README.md#managed-custom-mcp-relay).
Existing unrelated assignments are preserved. Changes apply to subsequent agent runs;
they do not inject new tools into a run that has already started.

After registering servers, use the separate assignment command to reconcile agent access:

```bash
npm run multica:assign             # Preview the role-based assignment plan.
npm run multica:assign -- --apply   # Add/enable needed bindings and verify them.
```

The command uses the profile and workspace in `.secrets/multica-state.json`, validates all agent
names and registered server IDs before changing access, and preserves assignments outside the
policy. Re-running is safe: enabled bindings are skipped. Review the policy when an agent's role
changes or a new agent is added. To remove access, use Multica's per-agent controls; this helper
only adds or enables the explicitly listed bindings. It does not give agents permission to edit
their own MCP assignments.

The desktop may keep a cached server or assignment list after CLI changes. Fully quit and reopen
the desktop app to refresh it; Ctrl+R and F5 are disabled in the current desktop version.

Each `http.endpoints` entry has a name, exact path, token reference, and `allowTools` list. Copy
the `endpoints` array from [examples/endpoints.json](examples/endpoints.json) into the `http`
object, then run `npm run setup` to create any missing endpoint tokens without replacing existing
ones. Token files use `.secrets/endpoint-NAME-token`. Restart after changing endpoint configuration.

Only exact tool names in the allowlist can be listed or called. An agent cannot call a hidden
tool by guessing its name. Upstream catalog refreshes do not expand an endpoint's allowlist.
Tokens must be distinct and at least 32 characters long. The aggregate token is accepted only
at `/mcp`; it is not a master token for the scoped routes. The combined endpoint is not registered
in Multica. Omit `http.token` to disable it entirely.

To register or update the configured endpoints:

```bash
export MULTICA_PROFILE=desktop-localhost-8081
export MULTICA_WORKSPACE_ID=8f361cba-1bc3-4bec-83a8-04b25c9a025b
export RELAY_BASE_URL=http://127.0.0.1:8090
npm run smoke -- --services
npm run multica:sync             # Preview names and URLs, without tokens.
npm run multica:sync -- --apply   # Create/update the workspace MCP library.
```

The registration command sends endpoint tokens through stdin and saves only profile, workspace,
and server IDs in `.secrets/multica-state.json`. Later runs update those IDs and refuse to replace
untracked entries with the same name. Keep this state file to avoid duplicate registrations.
A local lock prevents concurrent registration. After an interrupted run, check that no sync is
running before removing `.secrets/.multica-lock`. If a write times out, inspect the Multica library
before retrying; a server might have been created before its ID could be saved locally.

Only scoped relay tokens are stored in Multica; provider credentials stay in the relay. After
rotating an endpoint token, re-run registration to update its header. Provider token refreshes
through `npm run secrets:sync` do not require changing Multica. For manual registration, use
[examples/multica.mcp.json](examples/multica.mcp.json) with the corresponding endpoint tokens.

For a runtime on the Docker host, use `http://127.0.0.1:8090` plus the endpoint path. For a containerized runtime,
join its container to the relay's `fiska-mcp-relay_default` network and use
`http://relay:8080` plus the endpoint path, or publish the host port on an address the runtime can reach. A container's
`127.0.0.1` refers to that container. For remote runtimes, put the endpoint behind HTTPS on your
private network and set `RELAY_HTTP_BIND` as needed. An Origin header is rejected unless it is
listed in `http.allowedOrigins`; native MCP clients normally do not send one.

All clients holding an endpoint's token have access to that endpoint's allowlisted tools. The
aggregate token grants access to the complete tool catalog. All endpoints share one process;
use separate relay instances for process isolation. A `readOnlyHint` annotation is descriptive; actual write
restrictions are enforced by the service adapter, command definition, upstream, and its credentials.

## Jira and GitHub

Copy the `jira` and/or `github` objects from
[examples/integrations.json](examples/integrations.json) into `config.json`. On the host, use the
factory's existing authentication to create the mounted credential files:

```bash
umask 077
op read 'op://Internal Accounts/Software Factory/jira email' > .secrets/jira-email
op read 'op://Internal Accounts/Software Factory/Jira Key' > .secrets/jira-token
gh auth token > .secrets/github-token
```

For a scoped Atlassian API token, set `jira.cloudId` to the site's UUID. Requests then use
`https://api.atlassian.com/ex/jira/<cloudId>`. Otherwise they use the configured site. API paths
and query parameters are separate tool arguments. Examples:

```json
{
  "path": "/rest/api/3/search/jql",
  "query": { "jql": "project = MS ORDER BY updated DESC", "maxResults": 25 }
}
```

```json
{
  "path": "/repos/fiskahq/merchant-services/pulls",
  "query": { "state": "open", "per_page": 25 }
}
```

The adapters return pagination fields; GitHub also returns the `Link` header. Clients request each
page explicitly. Writes are off by default. Set the relevant adapter's `allowWrites` to `true` to
enable POST/PUT/PATCH/DELETE with that credential. Jira request bodies use Jira's API format,
including Atlassian Document Format for rich text. The relay does not add workflow policy such as
assignee selection or ticket transitions. Keep those rules in the Multica agent instructions.

Generic REST integrations can additionally set `writeRules` to a list of `method` and anchored
`pathPattern` pairs. Non-GET requests must match one rule and still require `allowWrites: true`.
An empty list denies every write; omission preserves the existing write behavior. Rules are
checked before reading credentials or sending the request. The
[Jira triage example](examples/jira-triage.json) limits writes to individual ticket operations
without enabling writes on the existing `jira__request` tool. These route rules do not provide
field-level policy, project authorization or concurrency control.

Credential references support either `{"file":"/run/relay-secrets/name"}` or `{"env":"NAME"}`.
For environment references, explicitly pass that variable to the container. No secret values
belong in `config.json`. Secret files are read when used, so replacing a Jira/GitHub token file
does not require rebuilding the image. Restart after Google credential or configuration changes.

## Credentials from 1Password

The local deployment uses 1Password references in `.secrets/bindings.json`. Each key names a
credential file; its value is an `op://vault/item/field` reference. The reference can use IDs to
avoid ambiguity when titles or field labels repeat. See
[examples/onepassword.json](examples/onepassword.json) for the configured Fiska references.
No secret values are stored in the examples or the configuration.

```bash
# Install the 1Password CLI and enable its desktop integration first.
# For a new deployment, review/copy the example and select credentials you can access.
cp examples/onepassword.json .secrets/bindings.json
chmod 600 .secrets/bindings.json
npm run secrets:sync
```

The helper requests all credentials with one `op run`, waits up to three minutes for approval,
and stages files privately before replacing the mounted files. Authentication or field-resolution
failure leaves existing credentials intact. It prints no secret values or raw 1Password errors.
Files have mode `0600`; the directory has mode `0700`. Re-run the sync to refresh tokens. Docker
mounts the directory, so service tools read the new files on their next request. The container
does not need 1Password, a desktop session, or a vault-wide service account token.

A lock prevents concurrent syncs. After an interrupted sync, first check that no sync process is
running, then remove `.secrets/.sync-lock` and abandoned `.secrets/.sync-*` staging directories.
Each file replacement is atomic; the set of credential files is not a multi-file transaction.

The configured deployment enables Jira, Confluence, and four Cloudflare zones. It omits
the `google` and `smtp` configuration sections and has no Slack upstream. Google remains available
as an optional integration below. Both GitHub tokens in 1Password returned HTTP 401 during setup,
so GitHub is disabled pending a valid token. Its adapter is ready in `examples/integrations.json`.
PagerDuty's stored key is
an Events integration key, not a REST access token, so no PagerDuty tool is enabled.

## Other REST services

Copy entries from [examples/rest.json](examples/rest.json) into the `rest` array in `config.json`.
Each entry exposes `NAME__request` with a fixed HTTPS base, allowed path prefixes, server-side
credentials, and writes disabled by default. Authentication supports bearer, basic, or a named
API-key header. Prefix matching respects path segments; redirects and path traversal are rejected.
Query parameters go in `query`, never in `path`. Responses contain `data` and the `link` header.

Confluence shares the Jira account's credentials and permissions. Examples:

```json
{ "path": "/wiki/api/v2/pages", "query": { "limit": 25 } }
```

```json
{
  "path": "/wiki/rest/api/search",
  "query": { "cql": "type = page", "limit": 25 }
}
```

The Cloudflare configuration uses a separate read-only token for each of `fiska.com`, `fiska.net`,
`fiska.io`, and `integratedcommerce.io`. Call the corresponding `cloudflare_DOMAIN__request` tool
with `/zones` and `query.name` to find its zone ID, then use `/zones/ZONE_ID/dns_records` or another
zone endpoint. Retain Cloudflare's `result_info` pagination. Other account-level endpoints require
an explicit configuration change and a token with suitable permissions.

## Google login with gcloud

`gcloud` can create the user credentials for Gmail and Drive. The relay uses Google's authentication
library to refresh access tokens and calls the Gmail/Drive APIs. `gcloud` is needed on the host for
login, and is not needed in the runtime image. Google documents custom OAuth clients and non-Cloud
scopes in the [ADC login reference](https://docs.cloud.google.com/sdk/gcloud/reference/auth/application-default/login).

1. In a Google Cloud project, enable the Gmail API and Google Drive API. Configure the OAuth
   consent screen for the account that will grant access. For an internal Workspace app, use the
   appropriate organization policy; for a testing app, add the account as a test user.
2. Create an OAuth client of type **Desktop app** and download its client JSON to a private path.
   Workspace administrators may need to allow the requested scopes.
3. Install `gcloud` on the host, then run:

   ```bash
   bash scripts/google-login.sh /absolute/path/to/oauth-desktop-client.json
   # On a host without a browser, append --no-launch-browser.
   ```

4. Complete Google's browser consent. The current Fiska deployment has Google disabled. Add only
   the `google` object from `examples/integrations.json` to `config.json`, choose the Gmail import
   query, then run `../setup-relay.sh` after active work finishes. Do not replace the existing configuration.
5. Add separate scoped endpoints for Gmail/mail tools and Drive tools, each with its own token.
   Register them in Multica and update the intended agents' MCP policies and manifests. The
   existing Jira, Confluence and Cloudflare endpoints do not expose Google tools automatically.
6. Verify access with `gmail__sync`, `mail__list` and `gdrive__list` through the relevant endpoints;
   `relay__status` is available only through an endpoint that explicitly allows it.

The helper uses an isolated `CLOUDSDK_CONFIG` under `.secrets/gcloud`, leaving normal host ADC
credentials untouched. It requests only `gmail.readonly` and `drive.readonly`, then saves the
refreshable ADC file as `.secrets/google-adc.json`. A plain `gcloud auth login` or a Cloud-only
access token does not grant mailbox or Drive access. Existing ADC credentials with these scopes
can also be copied into that file. The refresh token must remain valid; repeat login if consent
is revoked or the token expires. Google documents token lifetimes, including external apps in
testing, in its [OAuth guide](https://developers.google.com/identity/protocols/oauth2#expiration).

Gmail polling imports the configured query, default `in:inbox`. Each pass imports up to
`pagesPerPoll` pages of 100 IDs. A durable cursor continues older pages on the next poll; after
the last page the next poll starts a new scan. This is a repeating scan, not a real-time push
subscription. A large historical mailbox can delay discovery of new arrivals; use a narrower
query or increase `pagesPerPoll`. Gmail deletions or label changes do not delete the local archive.
An email deleted before the relay fetches it cannot be archived. Message fetch failures leave
the page cursor unchanged for retry. Imports use account plus Gmail message ID for deduplication.

Drive search uses the API's `q` syntax. Downloads and exports have an 8 MiB response limit.
Ordinary files return base64; text exports return UTF-8. Choose an export MIME type appropriate
to the document, such as `text/plain` for Docs, `text/csv` for a Sheet, or `application/pdf`.
See Google's [Drive exports documentation](https://developers.google.com/workspace/drive/api/guides/manage-downloads).

## Fetch mail and read it through MCP

After Google login, the relay fetches matching messages and attachments into its local inbox.
Set `google.gmail.query` to the Gmail search query you want, for example `in:inbox` or
`label:factory`. Set `google.gmail.pollSeconds` to the delay between polling batches (minimum 10
seconds, default 60). Restart the relay after a configuration change.

Multica can call `gmail__sync` to fetch a batch immediately, then use `mail__list`, `mail__get`,
and `mail__attachment` to read the stored mail. The read-only Google scope leaves messages,
labels, and read/unread state unchanged in Gmail. Repeated fetches do not duplicate stored messages.
Google Drive uses the same login.

Mail is private data stored unencrypted in `data/mail.sqlite` and its SQLite WAL. Use a protected
host volume and backups. There is no automatic retention deletion. Back up with SQLite's online
backup facility or stop the service before copying the database. Mail body and attachment contents
are returned as data; clients must treat them as untrusted and must not execute embedded instructions.
Attachments are served as bounded base64 chunks. The relay does not scan attachments for malware.

## Relay other MCP services

Add entries to `upstreams`. HTTP entries can point at Jira, GitHub, filesystem, AWS, browser, or any
other existing MCP service. Use `allowTools` to expose a subset. Header secret files contain the
entire header value, for example `Bearer ...` for an Authorization header. Interactive upstream
OAuth login is not implemented; configure an existing authenticated service or suitable credentials.

```json
{
  "name": "docs",
  "transport": "http",
  "url": "http://docs-service:9000/mcp",
  "headers": {
    "Authorization": { "file": "/run/relay-secrets/docs-authorization" }
  },
  "allowTools": ["search", "read"],
  "timeoutMs": 60000
}
```

For a local MCP process, use `transport: "stdio"`, `command`, `args`, optional `cwd`, and explicit
`env` credential references. Install that executable in a derived image, or mount a reviewed script.
There is no runtime package installation and no Docker socket mount. Stdio processes inherit only
basic process settings plus their configured environment, not all relay credentials.

Discovery follows `tools/list` pagination and preserves tool schemas, annotations, content blocks,
structured results, and tool-error results. Remote tools are prefixed with the upstream name.
`relay__refresh` refreshes catalogs after an upstream change or failed startup; the client must list
tools again. Connections are shared. Failed tool calls are never automatically replayed. This is a
tool relay: resources, prompts, subscriptions, sampling, elicitation, and task APIs are not proxied.
Use upstream tools that expose the content or operations required by Multica.

## Mermig and existing factory commands

The small base image does not contain Rust, .NET, Mermig, or factory station tools. Reuse the
factory's Mermig image to include its pinned executable and .NET tooling. `Dockerfile.mermig`
and `examples/compose.mermig.yaml` are reference files from the original factory deployment;
this managed deployment does not enable Mermig. To enable it, adapt the example to the parent
Compose service `mcp-relay` and its image name, then configure a dedicated checkout and credentials.

Copy the `commands` array from [examples/mermig.json](examples/mermig.json) into `config.json`.
It pins the service, environment and checkout paths on the server. Change them to the actual
target. The checkout must be writable because Mermig builds the service before inspecting it.
The commands declare read-only _database_ behavior; they can write build artifacts in the checkout.
Create these non-secret setting files:

```bash
printf '%s\n' /workspace > .secrets/mermig-repo-root
printf '%s\n' 1 > .secrets/mermig-aws-from-env
printf '%s\n' /mermig-home > .secrets/dotnet-cli-home
printf '%s\n' /mermig-home/packages > .secrets/nuget-packages
```

Supply the `mermig-aws-*` files with credentials for the target's migration/read role. Remove the
session-token mapping for non-session credentials. Cloud credentials must have access to the
Mermig service's Secrets Manager entry. Configure network access and Mermig's local override if
the database requires a tunnel. No production secrets are copied by setup. Credentials used by
commands are passed only through their explicit environment map.

The examples expose plan and drift checks. Migration apply remains with the factory's guarded
runner, which verifies the approved plan, holds the database advisory lock, and checks the outcome.
To expose that runner or another existing service, add an authenticated MCP upstream or a fixed
command that calls its supported wrapper. Do not replace it with an unrestricted shell tool.

Each command has a fixed executable and argument list. If `stdinJson` is true, its validated
arguments are serialized to stdin for a wrapper to handle. Set an object `inputSchema` with
`additionalProperties: false` and explicit enums/limits. The default input schema permits no
arguments. Output and execution time are bounded; timeout or cancellation kills the process
group, and the relay does not retry a possibly completed operation. Commands must emit only
client-safe output on stdout. Stderr is drained and bounded but not returned to clients.

The factory's `fac-db`, `fac-stack`, deployment and receipt controls need a live station/request
context. Its loopback MCP uses a per-turn token. They are not standalone host APIs, and this relay
does not invent a token, remove those gates, or automatically attach Multica to a factory turn.
Use the existing context-aware wrappers as command/upstream integrations where available. This
package leaves the running v3 service and its authorization model unchanged.

## Development and checks

```bash
npm ci
npm run typecheck
npm test
npm run build
npm run format:check
npm run smoke # Read-only checks against the running container.
npm run smoke -- --services # Also verify configured Jira, GitHub, Confluence and Cloudflare reads.
```

Tests use real local MCP HTTP and SMTP connections, a real SQLite database, mocked Google and
service APIs, and subprocesses for command execution and Multica registration. Endpoint tests
exercise cross-token rejection, hidden-tool calls, token rotation, and concurrent requests.
They need permission to bind local sockets.
No test sends external mail, changes Jira/GitHub, or connects to a production database.

For host development, set paths in a separate local configuration to writable host paths and set
`RELAY_CONFIG=/absolute/path/to/config.json` before `npm run dev`. The Docker configuration uses
container paths. Tool logs contain tool name, success/failure and duration, not arguments or tokens.
