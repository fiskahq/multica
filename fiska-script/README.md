# Multica Docker sandbox

Run the self-hosted Multica app and its agent runtime in Docker. The scripts use the repository's
`../docker-compose.selfhost.yml` and add the sandbox settings in `compose.sandbox.yml`.
The backend and runtime CLI are built from the fork checkout, including its worker-pool
extension. The frontend and PostgreSQL use upstream images. The scripts do not run `git pull` or
replace the source checkout. Keep the pool changes when you update that checkout.

## Guided setup for a new developer

This directory lives inside `fiskahq/multica`, alongside the worker-pool and Kiro credential
rotation changes. An upstream-only clone is not sufficient. Each developer
starts with a fresh workspace and their own credentials; do not distribute another installation's
`.env`, backups, state files, relay secrets or provider login data.

```bash
git clone git@github.com:fiskahq/multica.git
cd multica/fiska-script
./setup.sh
```

The Bash wizard asks for runtime CPU/RAM limits, concurrency, browser/API ports, optional QA
Docker and MCP relay services, GitHub access, the coordinator name, Claude/Codex authentication
methods, and a distinct 1Password reference and capability labels for each Kiro worker. It shows
Docker's available CPU/RAM first; on macOS these are the Docker Desktop VM's resources, not the
Mac's total resources. It checks memory headroom before continuing.

After reviewing the answers, the wizard writes private configuration, builds and starts the app,
guides browser account/workspace creation and personal access token login, provisions the team
and rotating Kiro key pool, then configures the selected optional services. Credentials enter
through native CLI login flows, 1Password or a hidden fallback prompt. API key values are never
written to `agents.json` or `wizard-state.json`. Resolved provider keys are stored in Multica's
agent environments as described below. GitHub setup also configures the Git HTTPS credential
helper inside Docker.

If the relay is selected, it can prompt for Jira/Confluence credentials, default to read-only
access, and register scoped MCP endpoints for the coordinator or all team agents. Existing relay
configuration is preserved. Slack, Google and other integrations require their own configuration;
this wizard does not claim to connect those accounts. QA setup verifies image builds, workspace
mounts, localhost access and cleanup. No paid model task is run automatically.

```bash
./setup.sh --configure-only  # Ask questions and save settings, without building or starting services.
./setup.sh --resume          # Reuse saved settings after fixing an interrupted installation.
```

Existing agent definitions, database/JWT secrets and unrelated `.env` settings are preserved.
An active runtime blocks reconfiguration. If it is idle, the wizard asks before stopping it.
An existing app is started using its installed images; use `update.sh` for source upgrades.
Browser login, consent and personal access token creation still require the developer.

On macOS, install Docker Desktop, Python 3 and Git. If Homebrew is already installed, the wizard
can offer to install missing prerequisites and open Docker Desktop. It does not install Homebrew.
The scripts support Bash 3.2 and use Python's OS file locks instead of requiring Linux `flock`.
The wizard and lifecycle tests run on Linux under Bash 3.2; a complete Docker Desktop installation
on a physical Mac, including Apple Silicon provider execution, still needs verification.

## Install the app without the wizard

Requirements: Bash, Docker with Compose 2.17 or newer, Python 3, Git, and OpenSSL. Use Docker Engine
on Linux or Docker Desktop on macOS. The current user must have access to Docker.

```bash
cd fiska-script
./install.sh
```

The installer creates `.env` with random database and JWT secrets and mode `600`, pulls the
frontend/database images, builds the backend and runtime images, and waits for the app to become ready. Existing `.env`
files and Docker volumes are preserved. The Compose project is named `multica-sandbox`.
To use another project name, export `MULTICA_PROJECT_NAME` consistently for all commands.

Open <http://localhost:3100>. The API is at <http://localhost:8180>. Both ports bind only to
`127.0.0.1`. PostgreSQL and the runtime have no published ports. To change ports, edit `.env`;
keep `FRONTEND_ORIGIN` and `MULTICA_APP_URL` aligned with `FRONTEND_PORT`, then run
`./sandbox.sh start` to apply the settings with the installed images.
Exported environment variables take precedence over `.env`, as with standard Compose.

Request a sign-in code in the app. Until you configure SMTP or Resend in `.env`, find the code
with `./sandbox.sh logs --tail 100 backend`. Create your workspace and a personal access token
in the app. The runtime starts separately after authentication.

## Configure the agent runtime

The runtime image installs all four coding CLIs at build time:

| Tool | Command | Installation source |
| --- | --- | --- |
| Claude Code | `claude` | [`@anthropic-ai/claude-code`](https://code.claude.com/docs/en/setup#install-with-npm) |
| Kiro CLI | `kiro-cli` | [Official Linux installer](https://kiro.dev/docs/getting-started/installation/) |
| Codex CLI | `codex` | [`@openai/codex`](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex) |
| OpenCode 2 | `opencode` | [`@opencode/cli`](https://opencode.ai/docs/) |

For manual agents outside the managed pool, authenticate providers inside the sandbox. Each command below starts
its own login flow; complete it before running the next command:

```bash
./sandbox.sh shell
claude auth login
codex login --device-auth
opencode auth login
exit

# Enter your Multica personal access token at the prompt.
./sandbox.sh login
./sandbox.sh start-runtime
./sandbox.sh logs --tail 100 runtime
```

The image also includes Node.js/npm, Git, GitHub CLI (`gh`), SSH, Python, a C++ build toolchain,
curl, ripgrep, and the Docker client with Compose and Buildx.
Open the sign-in URL in your host browser and follow the provider's code or token instructions.
Browser callback ports are not exposed. Codex device authentication must be enabled for your
account; see [Codex authentication](https://learn.chatgpt.com/docs/auth). Do not run the app's
host installer to connect this runtime: use the commands above so that its credentials stay
inside Docker. Login alone does not start an agent task.

For the managed Claude/Kiro squad, use the JSON setup below instead of the manual provider
login steps. Do not run `kiro-cli login` in the shared runtime: its browser session would override
per-agent API keys.

If a migrated Claude session expires and cannot refresh, copy the host's current subscription
login into the running runtime:

```bash
./setup-claude.sh
```

This checks the host login, copies only its Claude subscription credentials through stdin with private file permissions,
and checks the runtime login status. It does not restart the runtime or retry failed tasks.
All Claude agents in this shared runtime use the imported account. This is a one-time copy;
subsequent host login changes are not synchronized. To give Docker its own login, run
`./sandbox.sh shell` followed by `claude auth login` instead. Runtime MCP credentials are preserved.

For unattended jobs, generate a long-lived subscription token in your terminal with
`claude setup-token`, then run `./setup-claude.sh --token` and paste it into the hidden prompt.
Copy the raw token without Markdown escaping, quotes or backslashes. The installer rejects
formatted tokens. Do not paste tokens into chat or put them in command arguments. The script stores the token in
`env.CLAUDE_CODE_OAUTH_TOKEN` in the runtime's persistent `~/.claude/settings.json`, with mode
0600. New Claude processes load it without restarting the daemon. Existing processes keep their
current authentication. Explicit agent API keys and other higher-priority authentication still
apply. Once this token is installed, replace it with `--token`; the host-login importer refuses
to silently override it. Claude's local auth status does not prove a model request will succeed.
See [Claude authentication](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token)
for expiry and capability limits.

The runtime connects to `http://backend:8080` on the Compose network. It appears as
`multica-docker-sandbox` under Team > Runtimes. Create an agent that uses this runtime and its
installed provider. Provider authentication and Git access must be configured before assigning work.

`/home/node` stores Multica login state, provider credentials, and user-installed tools.
`/workspaces` stores cloned repositories and task output. Both are named Docker volumes. Use
`./sandbox.sh shell` to configure Git credentials and identity inside the container. Host home
directories, SSH keys, source trees, and the Docker socket are not mounted.

GitHub pull-request status requires API authentication in addition to Git's SSH access. To
reuse the host's existing GitHub CLI login in the running container:

```bash
./setup-github.sh
```

This copies the current GitHub token through stdin into the runtime's private configuration
under `/home/node`. Agents in the shared container can use this account; the credential remains
in the home volume across restarts. Alternatively, use `./sandbox.sh shell` and run
`gh auth login --hostname github.com --git-protocol ssh` to authenticate separately.
Use `gh pr view NUMBER --repo OWNER/REPO --json state,mergedAt,mergeCommit,url` to determine
whether a pull request merged. Git ancestry alone cannot detect all squash or rebase merges.

For the migrated Software Development team, Delivery Preparation includes creating or updating
the task PR after the exact-candidate review, QA and branch-finalization checks. Acceptance receives
the verified PR URL, target/source branches and reviewed SHA. A prepared title/body alone is not
sufficient, except for explicit local-only/packet-only requests or non-code deliverables. Missing
publication access blocks the handoff. PR creation does not authorize merge or deployment.
Apply this policy and the Jira-first branch naming convention with `./setup-delivery.sh`; it
preserves workflow routing, updates role and handoff instructions, and saves a before/after backup.
Run `python3 configure-release-mcp.py` to give this migrated release role the existing
`factory-jira-triage` write endpoint alongside Jira search and the Docker relay's Confluence
connection. Release updates linked Jira tickets with the PR, actual validation and implemented
Impact and Change Description. Jira writes are restricted by the relay's existing route rules;
Confluence permissions follow the relay configuration. The script verifies native assignments and instructions
without changing tickets or restarting services.
To enable Confluence writes for the release agent, run `./setup-confluence-writes.sh`,
then `python3 configure-release-mcp.py` to update the migrated release role and workflow. Only the
dedicated `confluence-write` service is started; existing containers and shared permissions remain
unchanged. It reuses the relay image with separate configuration, token and data, exposes no host
port, and is assigned only to development-release as `factory-confluence-write`. Release can create
or update task documentation, preserving content and reconciling page-version conflicts. Setup
start/stop/status commands include the service once `confluence-write-state.json` exists.

The four bundled tools live under `/opt/agent-tools` and `/usr/local/bin`, outside the home
volume. Their commands take precedence over user-installed copies. Existing home volumes
therefore cannot hide updated tools. To check the installed versions:

```bash
./sandbox.sh shell -c 'claude --version; kiro-cli --version; codex --version; opencode --version'
```

### Multiple agents

Create multiple agents in the same Multica workspace and select this runtime and the desired
provider for each. They can coordinate through issues and comments and share code through Git
branches and commits. Managed repository checkouts use separate work directories; later runs
on the same issue can reuse its directory. A project explicitly configured with a local directory
uses that directory directly. Do not assume uncommitted edits appear in another agent's checkout.

The container defaults to one task at a time. To permit more, set
`RUNTIME_MAX_CONCURRENT_TASKS=4` in `.env`, adjust `RUNTIME_MEMORY_LIMIT`, `RUNTIME_CPUS`, and
`RUNTIME_PIDS_LIMIT` for the workload, then run `./sandbox.sh start-runtime` when running and
queued tasks have finished. For four coding tasks on a host with enough spare capacity, start
with `RUNTIME_MEMORY_LIMIT=16g`, `RUNTIME_CPUS=8`, and `RUNTIME_PIDS_LIMIT=2048`. Each agent's
own concurrency setting in Multica also applies. Agents in this container share the home volume
and provider credentials; separate task directories do not isolate them from one another.

Run `python3 configure-agent-concurrency.py` to apply `concurrency-policy.json` without restarting
services. Planning, research, coordination and ticket roles allow 10 runs each; code review,
practices and release allow 4; builders allow 3; QA, adversarial testing, reproduction, Brains,
polling and coding workers allow 1. Unknown roles default to 1. Existing runs finish normally when a limit is
lowered. Saved limits are verified and previous values backed up. These are per-agent caps, not an
aggregate resource limit for heavy work. The policy's desired runtime total is 10; changing the
actual `.env` runtime limit still requires an idle runtime restart.

## Configure the Claude leader and Kiro pool

Install the [1Password CLI](https://developer.1password.com/docs/cli/get-started/) on the host
and sign in with `op signin`. Only the host installer reads 1Password; its token is not passed
into the runtime. Create the Multica workspace in the browser first.

```bash
cp agents.example.json agents.json
# Edit agents.json with your agent names, capability labels and op:// references.
./sandbox.sh login
./setup-agents.sh agents.json
```

On a new installation, `./install.sh --agents agents.json` builds the app and then starts the
same interactive setup. It prompts for a Multica personal access token if needed. You must
create the workspace and token in the browser before continuing that prompt. Run provisioning
while no agent tasks are active. It starts the daemon so its native runtime registrations exist.

The JSON creates one Claude agent with Codex available through `codex exec`, and one Kiro
worker per `workers` entry. The main agent is the native squad leader. Worker order in the JSON
is the pool order. Capability labels are explicit routing metadata, separate from Multica skills;
configure native skills and MCP tools through Multica as usual. Newly provisioned coordinators
default to 10 concurrent tasks and coding workers to 1, subject to `concurrency-policy.json` and
the runtime-wide limit.

Supported credentials:

| Type | Reference / setup | Destination |
| --- | --- | --- |
| `anthropic_api_key` | `op://...` | Main agent's `ANTHROPIC_API_KEY` |
| `codex_api_key` | `op://...` | Main agent's `CODEX_API_KEY`, used by `codex exec` |
| `kiro_api_key` | A distinct `op://...` reference per worker | That worker's `KIRO_API_KEY` |
| `claude_login` | Omit `reference`; interactive CLI login | Shared sandbox Claude credentials |
| `codex_login` | Omit `reference`; interactive device login | Shared sandbox Codex credentials |

For a migrated subscription login, add `"reuse_existing": true` to its `claude_login` or
`codex_login` entry. Setup checks the CLI login status and stops if it is unavailable. It does
not open another login flow or read a provider key from 1Password. The default still starts
the interactive login flow.

For subscription login instead of API keys, replace the main agent's credentials with:

```json
"credentials": [{"type": "claude_login"}, {"type": "codex_login"}]
```

The setup opens each login CLI and shows instructions. If a 1Password read fails in a terminal,
it offers a hidden API-key prompt. Noninteractive failures stop with instructions to rerun.
Kiro pool workers require API keys; a single shared browser login cannot represent separate
worker accounts. Setup clears the sandbox's Kiro browser session before provisioning. See
[Kiro authentication precedence](https://kiro.dev/docs/getting-started/authentication/) and
[Codex environment authentication](https://learn.chatgpt.com/docs/config-file/environment-variables).

Keys travel to Multica through stdin, never shell arguments, image layers or Compose environment
variables. The installer resolves each reference once per provisioning run. `agent-state.json`
contains IDs and configuration fingerprints, not key values. Successful unchanged credentials
are not read again on rerun or update. An interrupted, incomplete credential write can require
another read. Keep this state file to resume installation safely. To rotate a value at an unchanged
reference, remove that agent's `credentials` fingerprint from `agent-state.json` and rerun setup.

Multica's native custom environment fields are **plaintext in its database and database backups**.
This setup uses that existing behavior. Protect those backups and the runtime volume. Agents in
one runtime share the same OS user and filesystem; per-agent environment injection is not a
security boundary between agents.

Assign an issue to the squad in the browser. The leader receives a pool-specific protocol and uses:

```bash
multica squad delegate ISSUE_ID --pool kiro --request-id STABLE_DELEGATION_ID \
  --instructions 'Implement the requested change' --capability code
```

The backend selects a worker, records its delegation comment, adds a native deferred task and
advances the cursor in one PostgreSQL transaction. Concurrent calls serialize per pool. A retry
with the same request ID and input returns the same task; different input with that ID is rejected.
Worker replies continue through native squad coordination. Task execution and provider-side
external actions are not exactly-once guarantees.

Disabled, archived, unauthorized, offline or stale-runtime workers are excluded. Busy workers
retain their turn and queue work. If none qualify, the request fails without consuming a turn.
Rotation balances delegation counts within the eligible set, not tokens, duration or cost.
Changing membership or requested capabilities changes that set. The cursor persists across
restarts; changing the member order preserves its numeric position in the new order.

Direct invocation from a squad leader to its pooled workers is blocked by the backend's native
invocation gate. Human invocation and normal task retries remain available and do not consume
pool turns. The main agent is excluded from the pool. Use `enabled: false` on a worker to disable
it while retaining this routing restriction. Removing a worker from the JSON removes it from
the pool on the next setup; it does not delete the native agent or its work history.

Inspect or replace pool configuration through the CLI:

```bash
./sandbox.sh cli squad pool list SQUAD_ID
./sandbox.sh cli squad pool set SQUAD_ID kiro --file /dev/stdin < pool.json
```

`pool.json` has a `members` array with `agent_id`, `capabilities`, and optional `enabled` fields.
An empty array disables pool dispatch. Pool configuration requires a human squad manager;
agent tasks cannot change the rotation. The API contract is in
[`../server/worker-pools.openapi.yaml`](../server/worker-pools.openapi.yaml).

## Docker containers for QA

Enable a separate test-container daemon while the agent runtime is running:

```bash
./setup-qa.sh
./qa-smoke.sh
```

This installs the Docker client into the existing runtime without restarting it and starts
`qa-docker`, a rootless Docker-in-Docker service. The runtime image also includes the client
for future installs. The `multica-qa` Docker context uses client-certificate authentication;
the host Docker socket and host home directory are not mounted. Test images, containers, and
volumes live in `qa_docker_data`, separate from the host daemon's containers.

Docker's rootless DinD image requires a **privileged outer container** to allow its nested
user namespaces and mounts. Its daemon runs as UID 1000. This is an operational boundary,
not a VM-strength security boundary. See [Docker's rootless DinD documentation](https://docs.docker.com/engine/security/rootless/tips/#rootless-docker-in-docker).
All agents in the shared runtime can use this daemon. Its default aggregate limits are
8 CPUs and 12 GiB RAM when no overrides are set. The example environment and guided setup start
with 2 CPUs and 2 GiB; choose limits suitable for your test stack using `QA_DOCKER_CPUS` and
`QA_DOCKER_MEMORY_LIMIT`.
The main agent runtime keeps its separate resource limits.

The QA daemon shares the runtime's network namespace so ports published by nested containers
are reachable through `localhost` inside the runtime. It also mounts the workspace volume at
`/workspaces` and at the migrated workspace alias. Bind mounts must point inside those paths;
the runtime's private `/tmp` and `/home/node` are not shared with the daemon. For temporary
checkouts that will be bind-mounted, create a directory under `/workspaces` and set `TMPDIR`
to it before running the project script.

Inside the runtime, ordinary commands work:

```bash
docker info
docker compose version
docker run --rm alpine:3.23 echo qa-ready
# From a project checkout:
bash services/agent-env.sh up
bash services/agent-env.sh down --volumes
```

Parallel QA stacks need different Compose project names and published ports. Set
`COMPOSE_PROJECT_NAME` and the project's port variables before starting each stack.
Remove only that stack's containers and volumes when done. Ports are not published to the
host browser automatically; the `localhost` URLs here refer to the agent runtime.

Use `./sandbox.sh stop runtime` and `./sandbox.sh start-runtime` to stop/start both services
together. The start command reconnects the QA daemon to the current runtime network namespace
and refreshes its client certificates. Avoid recreating the runtime directly with ad-hoc
Compose commands while QA is enabled. `./sandbox.sh down` preserves QA data; deleting the
QA volume or running `docker system prune` inside the runtime removes test artifacts.

`setup-qa.sh` also installs an automatic connectivity check, including when called by the guided
wizard. Linux uses a user systemd timer; macOS uses a LaunchAgent. Every minute while the user
session is active, it tests the `multica-qa` TLS context from the runtime. This catches a Docker
restart-order problem where QA starts before the runtime and retains an old network namespace,
even though QA's own Unix-socket health check passes. An idle QA service is recreated in the
current namespace and its TLS client context is refreshed. The agent runtime is not restarted.
Both agents and the QA CLI continue using `tcp://127.0.0.1:2376`; no host Docker socket is exposed.

The check preserves intentionally stopped services, takes the setup operation lock, and refuses
to recreate QA while nested containers are running. If repair is blocked by active test stacks,
finish or tear down those owned stacks first; the next check retries. Other connectivity errors
are reported in the service log rather than causing repeated unconditional service restarts.
The same namespace check runs through `qa-client.py` during setup and normal runtime starts.

For an existing installation, install the check with `./setup-qa-watchdog.sh`. Run
`./repair-qa.sh` for an immediate check, or `./setup-qa-watchdog.sh --remove` to disable its schedule.
Keep the setup directory at the same path and rerun the installer after moving it. Linux was
verified live; the macOS LaunchAgent still needs an end-to-end check on a Mac. Run
`python3 qa_connectivity_test.py` for recovery safeguards and `./qa-smoke.sh` for a real build,
Compose container, workspace mount, localhost connection and cleanup test.

## Migrate an existing local instance

The migration tools require an idle source and an unused target project. They refuse to overwrite
existing target volumes. The source app is stopped during the copy; its database, uploads, and
host workspaces remain intact. Stop its host daemon before migrating. Do not operate both copies.

For the local instance migrated here, the commands are:

```bash
python3 migrate-local.py \
  --source-backend multica-backend-1 --source-frontend multica-frontend-1 \
  --source-postgres multica-postgres-1 \
  --workspaces /home/dave/multica_workspaces_desktop-localhost-8081 \
  --relay-origin http://mcp-relay:8080

python3 migrate-runtime.py --profile desktop-localhost-8081 \
  --ssh-identity gtihub_arch --ssh-identity gittea \
  --factory-dir /home/dave/dev/work/fiska/software-factory

./setup-agents.sh agents.json
```

These are migration commands, not routine restart commands. `migration-state.json` records the
last completed phase. `--resume-restored` resumes the application migration only when the source
app is still stopped and every restored table still matches the saved source fingerprints.

The database copy preserves all records and IDs, including issues, comments, skills and files,
workflows, squads, agent bindings, projects, chats, attachments, and autopilots. Every source
table is checked for identical row counts and content fingerprints before schema upgrades.
Uploaded files and complete task workspaces are copied into named volumes. A second volume
mount retains the original absolute workspace path so Git worktree links stay valid. It does
not mount the host directory. `compose.local.yml` records this alias; all setup scripts include
it automatically. The managed relay shares the normal Compose network.

Runtime import preserves the original daemon identity so existing agent runtime bindings still
work. It imports Claude/Codex login state, provider session history, selected SSH identities,
Git identity/URL settings, and the local Jira poller. The poller's copied configuration uses the
Docker API and relay addresses. The source files are unchanged. OpenCode is installed; configure
its provider login separately if you want to use it.

The new Coding squad has its own Claude coordinator and three Kiro pool workers. The four
original squads and their workflows keep their original agent roles and routing. Giving the
active original Kiro roles access to the installed worker keys is a separate credential-access
change. After approving that change, run:

```bash
python3 configure-migrated-agents.py
```

This enables task-level key rotation for the active original Kiro roles and switches their CLI
arguments to the API-key-compatible v3 engine. Archived agents stay archived. The migrated
instance has 15 active and 10 archived original Kiro roles. It does not fetch keys again.
The script removes fixed API-key overrides from the active migrated roles after configuring
the workspace credential pool. The original squads retain their roles and workflow routing.

### Round-robin credentials for every Kiro agent

`setup-agents.sh` configures workspace-wide key rotation after provisioning the workers.
For an existing installation, upgrade the backend and run:

```bash
./configure-kiro-pool.sh
./configure-kiro-pool.sh --status
```

The installed worker agents hold the credentials read during installation. At task dispatch,
the backend selects the next available credential for **every Kiro agent in the workspace**,
including migrated roles, chats, workflows, direct assignments, and future agents. It injects
the selected key into that task's environment without changing the agent's identity,
instructions, skills, or squads. Claude and other providers are unaffected.

The database locks the workspace cursor and commits the assignment with task delivery. A
replayed claim keeps its key, concurrent claims advance one shared cursor, and rolled-back
deliveries do not consume a turn. The assignment log stores source-agent IDs and key hashes,
not additional plaintext copies. Existing fixed role keys are overridden whenever this pool
is configured. The Coding squad's separate worker-selection round-robin remains in place.

A reported provider quota failure excludes that key from later selections. Already running
tasks keep their assigned key. No automatic task replay or paid overage is enabled. Retry the
failed task to obtain the next available key; if all keys are unavailable, dispatch fails with
an explicit error. Replacement key values are eligible automatically. After the provider
restores quota on an unchanged key, explicitly reset its exclusion:

```bash
./configure-kiro-pool.sh --reset-worker 'Kiro worker 3'
```

To import a verified quota failure from before rotation was enabled, while that role still
has its original fixed key, use `./configure-kiro-pool.sh --quota-task TASK_UUID`.
Rotation balances task starts; it does not enforce a separate concurrency limit for each key.

Migration backups contain the complete database dump, uploads archive, runtime credential/session
archive, source environment, and verification fingerprints. They contain secrets and use private
file permissions. The original host workspaces are retained; the migration does not make a third
archive of them. Back up the Docker workspace volume separately for disaster recovery.

For rollback before accepting new work, stop the sandbox first, restore the old containers'
restart policies from `backups/migration-*/source-containers.json`, and start the old backend and
frontend. Restart the old desktop daemon only after the Docker runtime has stopped. The source
remains on ports 3000/8081, and the sandbox uses 3100/8180. Work created after cutover is only in
the sandbox and must be exported before rolling back. Use the browser at port 3100 for the
sandbox; the old desktop profile still points to the stopped source.

## Update

Wait for active tasks to finish, then stop the runtime before updating:

```bash
./sandbox.sh stop runtime
# Update the source checkout deliberately, retaining the worker-pool changes.
# MULTICA_IMAGE_TAG selects the frontend release, not the custom backend.
./update.sh
./sandbox.sh start-runtime
```

The update refuses to proceed while the runtime is running. It pulls frontend/database images, rebuilds the
modified backend and runtime without the build cache, writes a PostgreSQL custom-format dump under `backups/`,
and recreates the app. Rebuilding refreshes all four bundled coding CLIs.
Migrations run through the upstream backend entrypoint. The fork's pool migrations use numbers
657-668. Upgrades from the earlier 630-641 pool migrations preserve existing tables, indexes,
rotation positions and quota records; the old migration entries remain as history. Readiness checks include database and
migration status through `/readyz`. A failed pull, build, or backup aborts before containers are
replaced. A failed startup reports an error; there is no automatic schema rollback.
Use `update.sh` for upgrades of an existing installation: the installer also fetches images,
but only the update script creates a database backup.

Updates retain database records, uploads, runtime credentials, repositories, and user-installed
tools. Set `CLAUDE_VERSION`, `KIRO_VERSION`, `CODEX_VERSION`, and `OPENCODE_VERSION` in `.env`
to exact release versions to pin them. The default `latest` resolves during a fresh build;
OpenCode uses the version 2 package. Kiro uses its stable release channel and verifies the
downloaded archive's checksum through its official installer.

Bundled binaries are read-only while the container runs. Use `update.sh` to update them.
Multica and Claude automatic updates are disabled. Extra tools installed in the runtime home
remain your responsibility to update with their own package manager.

`update.sh` backs up the database only. Back up uploads and both runtime volumes separately
if you need full disaster recovery. Preserve `.env` with those backups, and use matching app
images when restoring a database. Do not change the PostgreSQL password in `.env` without also
changing the password in the existing database. Changing the JWT secret invalidates sessions.

The repository checkout controls the upstream Compose definition. Update it deliberately when
needed; these scripts do not run `git pull` or overwrite local changes.

Builds derive the Multica CLI version from the checkout's nearest release tag and include its
commit ID. Keep upstream tags available, including when using a fork:

```bash
git -C .. fetch --no-write-fetch-head https://github.com/multica-ai/multica.git 'refs/tags/v*:refs/tags/v*'
```

This fetches version metadata without changing checked-out source files. A build with no release
tags stops with an error. Labels such as `worker-pools` are not valid CLI versions and cause
the app to block Create with agent and warn that project descriptions are unsupported.

## Managed custom MCP relay

The custom relay's source, tests and build files live in `mcp-relay/`. It runs as the
`mcp-relay` service in this Compose project. Agents use `http://mcp-relay:8080`; the host uses
`http://127.0.0.1:8090`. Only the host loopback interface exposes the port. The relay runs as
UID 1000 with a read-only root filesystem, no added capabilities and a 1 GiB memory limit.

Private deployment files live under `mcp-relay/config.json`, `mcp-relay/.secrets/` and
`mcp-relay/data/`. They are ignored by Git and excluded from the image build. The configuration
and credentials are mounted read-only; data is writable. No source-directory mounts from the
original software-factory deployment are needed after import.

To import an existing deployment, wait for agents using it to finish, then:

```bash
docker stop fiska-mcp-relay-relay-1
python3 import-relay.py --source /path/to/original/mcp-relay --container fiska-mcp-relay-relay-1
./setup-relay.sh
```

The importer requires the original container to be stopped and verifies its mounts. It copies
configuration, credentials and data with private permissions, refuses to overwrite an existing
destination and retains the original files for rollback. Disable the old container's restart
policy after a successful cutover. Preserve workspace MCP server IDs and agent assignments when
changing their URLs to `http://mcp-relay:8080`; retain the endpoint paths and authorization headers.

For a new deployment, run `npm run setup` in `mcp-relay/`, then configure its integrations before
running `./setup-relay.sh` from this directory. The example includes Google support, which needs
its own login. An imported configuration keeps its existing enabled services and permissions.
See [the relay guide](mcp-relay/README.md) for provider configuration and credential refresh.

Once enabled, `relay-state.json` includes the relay in `install.sh`, `update.sh`,
`sandbox.sh start`, `sandbox.sh start-runtime`, `sandbox.sh status`, `sandbox.sh stop`, and
`sandbox.sh down`. `sandbox.sh stop runtime` leaves the relay and app running. To rebuild only
the relay after editing its source, wait for active work to finish and run `./setup-relay.sh`.
Back up its configuration, secrets and data as well as the Multica volumes. Stop the relay before
copying its SQLite database and attachments. `update.sh`'s database backup covers PostgreSQL only.

```bash
./sandbox.sh logs --tail 50 mcp-relay
curl --fail http://127.0.0.1:8090/healthz
cd mcp-relay
npm ci --ignore-scripts
npm run typecheck
npm test
npm run smoke -- --services  # Auth checks and read-only provider probes.
```

## Architect ticket-writing agent

After the runtime is running and Claude is authenticated, install or update Architect:

```bash
./setup-architect.sh
```

This creates a workspace-accessible Claude Opus 5.5 agent with high effort. Its concurrency follows
`concurrency-policy.json`, currently 10 runs.
It attaches the bundled `ticket-creation` skill and, when available, the workspace's
`research-with-sources`, `architecture-designer` and `conventions` skills. It reuses scoped
`factory-jira`/`factory-confluence` MCP registrations, falling back to `relay-jira`/`relay-confluence`.
Use repeated `--mcp-name NAME` options to select other existing context registrations. Missing
optional skills or default MCP registrations do not prevent a fresh workspace install.

The installer uses native Multica commands and verifies the saved settings and attachments. It
does not restart services or run a model request. Keep `architect-state.json` with this installation;
it tracks managed IDs so reruns update the same agent and skill. Untracked name collisions are
rejected. Additional manually attached skills and servers are preserved on reruns.

Open Architect's chat or assign an intake issue for specialist research before finalizing tickets.
Architect uses native issue mentions, records outstanding questions and ends its run while other
agents respond. Consultation uses the requesting user's access and existing Kiro key rotation.
New planning work stays in backlog unless the user supplies routing. Multica's **Create with agent**
mode follows its built-in single-ticket capture rules first; it has no issue thread for consultation
before creation. Use a subsequent refinement run for deeper research.

The maintained [instructions](agents/architect/instructions.md),
[ticket skill](skills/ticket-creation/SKILL.md) and [research sources](agents/architect/sources.md)
are included here. The installer does not copy an existing developer's issues or specialist team
into a new workspace; Architect discovers the agents actually available there.

## Implementation advisor

Run `./setup-advisor.sh` with the Kiro runtime online to install or update **development-advisor**
on Kiro Opus 5.5 with high effort. It uses the existing per-task Kiro credential rotation and has
no separate credential copies. The current concurrency policy allows 10 advisory runs, subject to
the daemon's total limit. Setup makes no model request and does not restart services.

The advisor gives builders repository-grounded file/symbol change plans, interface and data
contracts, implementation ordering, tradeoffs and proposed tests. It does not implement or run
builds/tests. The bundled `implementation-planning` skill is required; existing research,
conventions, architecture and domain-modeling skills are attached when available. Context MCPs
reuse `factory-jira`/`factory-confluence` or `relay-jira`/`relay-confluence`; these registrations
must retain their read-only policy. No writer registration is attached. The no-code restriction is
an agent instruction, not an operating-system permission boundary in the shared runtime.

When present, the installer adds the advisor to Software Development and adds consultation
instructions to builder, lead, planner, Architect and the Software Factory Development step.
Native mentions let agents ask a bounded question without changing ownership or workflow routing.
Callers end their run while waiting; the advisor replies to the original comment to resume them.
Small fixes do not require consultation. New work uses the updated instructions; current runs are
not interrupted. Missing squad/workflow/optional skills are allowed in a fresh workspace.

Keep private `advisor-state.json` with this installation. Reruns use the same agent and skill IDs;
untracked name collisions and modified consultation sections require reconciliation. Existing
instruction changes are backed up under `backups/`; unrelated content and attachments are preserved.
Sources and role instructions live in [agents/development-advisor](agents/development-advisor/).

## Delivery PR and branch naming

Run `./setup-delivery.sh` to configure the existing development team for PR creation during
Delivery Preparation. It updates native role instructions, the shared delivery-handoff skill and
workflow guidance, preserving routing and exact-candidate review requirements. It creates no PR
itself and does not interrupt current runs.

The release agent must create or update the task PR and verify its open state, URL, target branch
and reviewed head SHA before Acceptance. Final branches always use
`feature/<JIRA-KEY>-<short-description>`, for example `feature/MS-123-transaction-reporting`.
Only when the user explicitly opted out of Jira, use the originating Multica identifier, for example
`feature/FISK-42-transaction-reporting`. The description is lowercase kebab-case and the issue key
is uppercase. Fixes and maintenance also use `feature/`. The reviewed SHA remains unchanged.
Existing PRs and active/dirty worktrees retain the shared skill's preservation rules; setup does
not rename current Git branches. Merge and deployment require separate task authorization.
These are verified agent instructions, not a server-enforced status gate.

## Jira prerequisite for every ticket

Every Multica ticket needs a matching Jira ticket unless the user explicitly opts out. Intake
searches for an existing scope match or creates the missing ticket, records the primary Jira key
and URL in Multica, and adds a Multica backlink in Jira. Capture can happen first, including manual
and quick-create tickets; implementation must wait for the mapping or a cited user exemption.
Missing access or an ambiguous Jira destination blocks implementation with an owner and resume step.
`/decisions auto` does not opt out of Jira. Child tickets need a verified mapping for their scope.

Agent setup, Architect, advisor and delivery setup apply this policy automatically. For an existing
installation, or after adding agents/workflows manually, run:

```bash
python3 configure-jira-policy.py --dry-run
python3 configure-jira-policy.py
# From the fork's fiska-script folder, target a separate private deployment:
python3 configure-jira-policy.py --deployment-dir /absolute/path/to/existing/setup
```

The installer updates agent/squad instructions, ticket-creation and delivery-handoff skills when
present, and assigned nonterminal workflow checkpoints. Unassigned steps cannot store instructions
in Multica; the coding agent's preflight still applies when work is assigned manually.
It attaches the existing scoped
`factory-jira-triage` write endpoint to Architect, development-release and Guy when available,
preserving other attachments.
Other agents can delegate Jira intake to Architect. A fresh install uses its configured Jira
endpoint; enable writes in the relay wizard to allow creation. Missing write access blocks intake.
The installer preserves routing, credentials and runtime
settings, backs up changed text, and verifies readback. The background learner remains a tool-free
draft reviewer; its captured tickets must also satisfy intake before any development. No historical
Jira tickets are bulk-created. Existing active runs receive the policy on their next run; setup does
not interrupt them. New roles/workflows added outside these installers need the command rerun.

This uses native instructions, not a server-enforced creation or status-transition gate. Agents
must follow the preflight; the API does not prevent a manual move into Development. Duplicate
prevention uses existing-ticket lookup, a designated intake owner and recovery after uncertain
creates; it is not a transactional cross-system uniqueness guarantee. Do not edit the same
instructions while installing: read-before-write detects intervening edits but is not an atomic
compare-and-swap. Review backups if an installation stops partway through.

### Jira and other relay tools on Kiro v3

Kiro v3 can declare HTTP and SSE MCP unsupported. Registering an HTTP endpoint then leaves its
tools unavailable even though the agent's attachment is enabled. Docker setup uses
`relay-stdio.py` to forward stdio MCP requests to the same scoped HTTP relay endpoints. The runtime
image includes the adapter; the wizard and relay sync register stdio by default. Tool names,
endpoint tokens, allowlists and Jira permissions remain the same. Headers travel in MCP environment
configuration, never command arguments. The adapter rejects redirects and does not retry writes
after a timeout or connection failure; agents must reconcile the result before retrying.

For an existing running installation, install the adapter and convert its locally configured
relay registrations without restarting the runtime:

```bash
python3 setup-relay-stdio.py --deployment-dir /absolute/path/to/existing/setup
python3 configure-jira-policy.py --deployment-dir /absolute/path/to/existing/setup
```

The first command preserves registration IDs and agent attachments. The second ensures the intake,
release and Guy roles have the scoped Jira writer. Fresh runs receive the new transport. Active runs
keep their original tools until they finish. Supported ticket writes include creation, field edits,
comments, assignment, transitions and issue links; existing restrictions on administrative and
destructive operations remain in the relay. A configured tool does not override the task's scope.

### Scoped Confluence writes and Jira release management

After configuring Jira and running `setup-confluence-writes.sh`, run:

```bash
bash setup-collaboration-tools.sh --deployment-dir /absolute/path/to/existing/setup
```

This attaches the ticket writer, Confluence writer and a separate `factory-jira-release` tool to
**Guy, Architect and development-release** when those agents exist. Other agents keep their current
attachments and can delegate these operations. The background learner remains excluded. Setup
backs up the relay configuration, briefly reloads only the main relay when required, installs the
stdio adapter, and updates native instructions. Run it between active tasks; it does not restart
the agent runtime. Repeating setup preserves registration IDs and does not duplicate attachments.

The release tool can find, create, edit, release, unrelease, archive, unarchive and reorder project
versions. It denies release deletion, merging and unrelated Jira writes. Ticket writers do not gain
release management through their existing endpoint. Confluence edits use the current page version
and must preserve unrelated content. All changes remain subject to the assigned task and the
connected account's permissions. Existing sessions need a fresh run to receive newly attached tools.

## Docker disk maintenance

Inspect and clean regenerable Docker data without stopping agents:

```bash
./cleanup-docker.sh --report
./cleanup-docker.sh --apply
./setup-docker-cleanup.sh          # Install daily maintenance for the current Docker context.
./setup-docker-cleanup.sh --remove # Disable that schedule.
```

Acceptance cleanup is automatic in the supplied runtime image. Once a ticket enters the
`acceptance` status, a daemon worker checks it within about one minute and waits until its agent
work finishes. A per-issue guard prevents a new task from starting during cleanup, including a
new task that uses a different workspace directory. Returning the ticket to development can
require reinstalling dependencies and rebuilding its test stack.

Ordinary task Docker commands pass through an ownership wrapper: containers and direct builds
get issue/workspace labels, and Compose projects get a stable ticket-specific name. Cleanup
removes those containers, their anonymous volumes, owned named volumes/networks and unused
built images from the `multica-qa` daemon. Shared pulled base images and build cache remain under
periodic maintenance. Use `docker` from PATH; calling `/usr/local/bin/docker` or a remote Docker
API directly bypasses ownership tagging. Explicit Compose project names are scoped to the
current ticket, so obtain the effective name from `docker compose config` when needed.

The same cleanup removes untracked dependency/cache directories (`node_modules`, `.next`,
`.turbo`, `.venv`, `__pycache__`, `.pytest_cache`) from daemon-owned ticket workspaces.
Source files, Git history, tracked files, nested repositories, symlink targets, output and logs
are retained. Task metadata and a small cleanup receipt provide retry/idempotency state; no AI
call or separate service is needed. Failed cleanup is retried, and a new completed task makes
its resources eligible again. Old Compose resources are eligible only when their recorded
working directory proves ownership; unlabelled resources with no ownership evidence are retained.
Scratch directories outside the managed ticket workspace are not deleted automatically.

The runtime image sets `MULTICA_ACCEPTANCE_CLEANUP_COMMAND` to its installed helper. Updating
an existing runtime requires the rebuilt image and daemon restart; new installs get it by
default. Removing that environment variable disables Acceptance cleanup. The helper receives
only issue/workspace IDs and proven root paths, and runs under the daemon's cleanup guard.

For an existing Software Development team, `./setup-qa-policy.sh` installs complete QA reporting
and a fix-planning consultation with `development-planner`; use `--dry-run` to preview the update.
`./setup-delivery.sh` also installs this policy. QA runs every required, runnable suite without
stopping at the first failure, records blocked/skipped checks separately, and publishes a complete
report tied to the tested commit. Before returning defects to Development, QA requests a plan on
the same issue and waits for the planner's response. The builder receives both the report and plan.
The installer updates the QA, planner, builder and leader instructions, squad guidance, and the
Software Factory QA/Development steps when present. It backs up changed fields and verifies
readback without changing workflow routing or starting tasks. These are agent instructions,
not server-enforced test gates; they take effect on new runs. Existing runs are not interrupted.

The separate scheduled maintenance below removes only dangling images older than 48 hours and old unused build cache. The cache
size targets are 20 GB on the host and 10 GB in the separate QA Docker daemon. Recent and in-use
records are retained, so these are best-effort targets rather than hard quotas. Tagged images,
all containers, all volumes, repository workspaces, credentials and backups are preserved. Old
cache removal can make subsequent builds slower. Docker's image and cache usage may overlap;
do not add those reported totals as independent disk usage.

Linux uses a user systemd timer around 04:00 with up to 30 minutes of jitter and catch-up after
login. macOS uses a user LaunchAgent at 04:15 while logged in. Docker must be running; an unavailable
Docker context is skipped. The scheduler pins the Docker context selected during installation;
manual cleanup defaults to `default`, or accepts `CLEANUP_DOCKER_CONTEXT`. The setup directory must
remain at the same path. Run setup again if it moves. Linux scheduling was exercised on the live
installation; the macOS path still needs validation on a Mac.

For manual runs, `CLEANUP_MIN_AGE=168h`, `CLEANUP_HOST_CACHE=30GB` and `CLEANUP_QA_CACHE=15GB`
can adjust retention. Minimum age cannot be lower than 48 hours. Scheduled defaults are recorded
in the generated user service/plist. Cleanup takes the setup operation lock so it does not overlap
managed install/update builds. It does not run volume/container prune or bulk repository deletion.
QA jobs should still tear down only their own uniquely named temporary Compose stack when done.
The runtime's native workspace retention remains responsible for task directories and Git evidence.

See [Docker pruning](https://docs.docker.com/engine/manage-resources/pruning/) and
[build cache retention](https://docs.docker.com/reference/cli/docker/buildx/prune/).
Run `bash cleanup-docker.test.sh` to verify cleanup scope and retention guards with a mock Docker CLI.

## Manage

```bash
./sandbox.sh status
./sandbox.sh logs -f backend
./sandbox.sh cli --version
./sandbox.sh shell
./sandbox.sh stop                # Stop all services, retain containers and volumes.
./sandbox.sh down                # Remove containers/network, retain all volumes.
./sandbox.sh start               # Start the app again using installed images.
./sandbox.sh start-runtime       # Start an already configured runtime.
```

The runtime has a read-only root filesystem, runs as UID 1000, drops all Linux capabilities,
blocks privilege escalation, and has CPU, memory, and process limits. Writable areas are its
two volumes and temporary memory-backed storage. Backend and frontend capabilities are also
dropped. These settings use the [Docker Compose service controls](https://docs.docker.com/reference/compose-file/services/).

This is a container boundary, not a virtual machine or a network firewall. Outbound internet
and reachable LAN services remain accessible. Tasks in this runtime share its credentials and
volumes. Docker builds inside agent tasks require the optional QA Docker daemon described above.

## Verify scripts

```bash
bash -n ./*.sh
bash ./scripts.test.sh
python3 -m unittest discover -s . -p '*_test.py'
```

The regression tests use a fake Docker command. They check secret preservation, update backup
failure, running-runtime protection, and concurrent operation locking without starting agents.

## Source layout and existing installations

The supported source location is now `fiska-script/` inside the Fiska fork. The previous separate
setup repository is no longer needed for new installations. Existing installations can continue
running from their old directory until a planned migration; do not run a fresh setup over an
active stack. Moving the source does not move private configuration or change running containers.

Before relocating an existing installation, stop its runtime and owned QA stacks, back up its
private configuration and data, and transfer the ignored configuration/state files and relay
secrets to this directory. Review local Compose overrides for absolute paths, then verify the
resolved configuration before restarting. Reinstall the cleanup and QA watchdog schedules from
the new location and remove the old schedules so maintenance uses only one installation path.
Keep the same `MULTICA_PROJECT_NAME` to retain the existing named volumes.

## Background learning

`./setup-learner.sh` installs one private Kiro **background-learner** and a small Docker service.
It uses native Multica agent execution and a private **Learning Review** project. It does not run
Hermes. New installs use **Opus 5.5 with high reasoning effort**. Use `--model MODEL` and
`--thinking-level low|medium|high` to change the selection; later installs preserve saved choices.
To upgrade an existing Haiku learner, run
`./setup-learner.sh --model claude-opus-5.5 --thinking-level high`.
Kiro uses the existing credential pool.

The installer requires a backend built from the synced fork containing upstream private-project
Autopilot protection (`c3a9d8fd1`). It verifies the stamped version against Git ancestry and refuses
unverifiable builds. `--prepare-only` creates the agent without starting runs. Updating only the CLI
is insufficient. The installer never restarts the backend or runtime.

```bash
./setup-learner.sh
./setup-learner.sh status
./setup-learner.sh candidates
./setup-learner.sh show SOURCE_TASK_UUID
./setup-learner.sh stop
./setup-learner.sh start
```

For an existing deployment whose private state remains elsewhere, add
`--deployment-dir /absolute/path/to/existing/setup` to each command. The running service mounts its
code from this checkout, so keep the checkout at that path. Fresh installs use this folder directly.

Completed and failed **issue runs**, across accessible agents, are discovered every 60 seconds from
paginated native task history. This first version uses asynchronous reconciliation rather than a
new completion webhook in the backend. Runs completed before initial installation are excluded;
runs already active at installation are included when they finish. Private chat sessions and the
learner's own runs are excluded. Deleted history cannot be recovered.

SQLite stores at most 500 pending/active items on persistent storage, with one active learner job.
Atomic transactions enforce the bound, task IDs deduplicate discoveries, and a process lock prevents
multiple workers dispatching the same active job. When full, new items stay in Multica history and
are discovered on later scans. HTTP retries reuse the persisted body and native webhook idempotency
key. A confirmed failed/skipped run or invalid output retries after 60 then 120 seconds, up to three
attempts; exhausted items remain visible as `dead`. An uncertain network response keeps the same
active item and key instead of starting another run. The learner consumes one of the runtime's
existing task slots. The sidecar is limited to 0.5 CPU and 512 MiB RAM.

The evidence packet contains a bounded, redacted transcript excerpt and recent issue comments.
Long transcripts may omit earlier context. Candidates must cite the source task and supplied
message sequence numbers, distinguish claims from verified results, and state applicability and
validation limits. The queue saves the source project and agent independently of model output.
It clears the prompt copy after processing; native Autopilot history still retains its payload.

Validated drafts automatically become unassigned **Todo** tickets in the private **Learning Review**
project. Open that project's issue list to review the lesson, rationale, evidence, applicability,
validation limits and review checklist. Existing queued drafts are backfilled without another model
call. Runs that return `no_lesson` or fail validation do not create tickets.

Ticket receipts prevent repeats across restarts and concurrent syncs. If a create response is lost,
the service searches for the source-task marker, including closed tickets. An uncertain creation
that cannot be found remains visible as `creation_outcome_unknown` in `status`; it is never blindly
submitted again. Inspect the server and receipt before recovery. Keep the marker in the ticket
description until the receipt has its issue ID. Recorded tickets are not rewritten or recreated
after human edits, completion or deletion. The project's workflow must have Todo and no automatic
handlers, so reviewing a lesson does not trigger development work.

You can also inspect drafts using `candidates` or the Autopilot run history.
A reviewer must check the cited evidence, narrow the scope, remove sensitive details, and check for
an existing equivalent skill. The worker posts a copyable approval command on each review ticket:

```text
/learning approve CONTENT_HASH
```

Only a member comment from the installation owner's account (the API credential's `/api/me` user)
is accepted. Agent and task-backed comments cannot approve. Copy the full command from the latest
Learning automation comment; the hash binds the ticket's exact text and generated skill content.
Edit **Proposed lesson**, **Applicability**, or **Validation limits** before approving. Edits produce
a new command on the next scan and invalidate earlier commands. Marking a ticket Done is not approval.
The worker checks approvals every 60 seconds. `/learning hold` supersedes a pending approval, but
does not undo work already applied.

Approval creates a separate native `learned-SOURCE_TASK_UUID` skill and attaches it to the source
agent. To select different agents, append ` agents=AGENT_UUID,AGENT_UUID` to the approval command.
The skill is **workspace-visible**. Project scope is preserved as an instruction in the skill, not
as a runtime access restriction. Only the reviewed procedure, applicability and validation limits
are published; the raw transcript and draft rationale are not copied into the skill.

The publisher records the approver, approval comment, content hash, native skill ID/version and
verified agent attachments, then posts a receipt on the review ticket. Existing skills are not
replaced or automatically merged. Native unique skill names and additive attachments support retry
after a lost response. Partial application resumes from its receipt; operator edits, disabled skills
or recorded attachments that were removed cause a block instead of being overwritten. Check
`./setup-learner.sh status` for publication state and errors.

Applied skills are available on subsequent runs; existing runs are not restarted. To undo
application, remove the skill from the listed agents in Skills settings. Completed receipts never
reapply a removed skill. One review ticket has one immutable publication; later ticket edits do not
republish. Further revisions or merges into shared skills use Multica's native Skills editor and
version history. Manual publication and attachment remain available.

After pulling learner service changes, run `./setup-learner.sh stop` then `./setup-learner.sh start`
to reload the worker code. This only restarts the queue service; native agent runs continue and are
reconciled on startup.

`learner-private/` contains the owner API credential, SQLite database, and sensitive drafts. It is
ignored by Git, created with owner-only permissions, and mounted only into the learner container.
The service has no Docker socket or provider credentials. Redaction is best effort, so keep the
review project private and restrict its membership. The service checks that project visibility and
the verified backend version still match before each new dispatch. After a backend upgrade, rerun
`setup-learner.sh` to verify compatibility and refresh its credential and configuration.

The status command reports queue counts and the last scan/worker heartbeat. A dead item needs
operator inspection; there is no automatic deletion of drafts or completed receipt IDs. Back up
`learner-private/` while the service is stopped, or use SQLite's online backup API. Do not delete
receipts merely to reduce queue counts: they prevent repeated learning from the same source run.

Verification, without paid model calls:

```bash
python3 -m unittest discover -s learner -p '*_test.py' -v
```

## Per-flow decision policy

Post one of these as a separate top-level comment on the root ticket:

```text
/decisions human
/decisions auto
/decisions @Architect
```

`human` is the default. `auto` authorizes the working agents to consult appropriate specialists
and resolve critical decisions. `@agent_name` delegates those decisions to one named agent.
The web editor's native agent mention also works. The ticket's human creator/requester, its
human project lead, and workspace owners/admins may change the policy. Agent task credentials
cannot change it. Invalid, inaccessible, archived, unconfigured, or ambiguous agent selections
leave the policy unchanged.

Child tickets inherit the current root policy, including children without their own project
workflow. Post changes on the root, not a child. Reparenting changes which flow a ticket belongs
to. Commands are saved with the policy in one database transaction, remain in the comment audit
history, and cannot be edited or deleted. Post `/decisions human` to revoke automatic decisions.
Quoted commands, issue descriptions, and metadata do not set policy. Concurrent commands are
serialized by the root issue's row lock; the last committed command wins.

The backend injects recurring checkpoints into named workflow briefs: requirements, design and
planning, each handoff, new evidence, and delivery/acceptance. Agents must record questions,
options, recommendations, evidence, affected requirements/commit, decision owner, and resume
steps. They route questions through native comment threads and release their execution slot
while waiting. Routine work and independent tasks can continue. A material change reopens the
relevant decision. `auto` does not bypass existing merge, deployment, production, spending,
external-communication, review, or acceptance requirements.

Agents read the current policy at each checkpoint with:

```bash
multica issue get FISK-123 --output json
```

The detail response's `decision_policy` contains `mode`, `root_issue_id`, `available`, and, after
an explicit command, `configured_by` and `comment_id`. Agent mode also contains `agent_id` and
`agent_name`. A selected agent becoming unavailable leaves mode unchanged with `available: false`;
the instructions require waiting for a human rather than silently enabling automatic decisions.
Older backends omit this field. It is also omitted when the root is in a private project the
reader cannot access; the child remains readable. Task briefs fail closed for that case. Missing
policy or policy-read failures must not be treated as automatic approval.

Policy changes apply to pending and future decisions when an agent next checks the policy.
They do not start a run, redirect an already running turn, resolve a question, or advance a blocked
ticket. Resume work through the existing assignment/comment workflow. The server enforces policy
ownership and persistence. Identifying critical decisions, routing questions, and observing the
checkpoints are agent instructions, not a database-enforced status-transition lock.

This feature ships in the fork's backend, including migration 669, and is built by the standard
setup/update scripts. It needs no separate sidecar, CLI update, or per-agent configuration.

## Internal Admin access and transcript inbox

Install the Jira/Confluence relays first, then configure the existing Software Development Management
squad. Grant its actual agent members Jira ticket and Confluence page read/write access with:

```bash
python3 configure-admin-access.py --deployment-dir /absolute/path/to/setup
bash setup-transcripts.sh --deployment-dir /absolute/path/to/setup
bash setup-transcripts.sh status --deployment-dir /absolute/path/to/setup
```

The transcript installer also applies that access policy. It preserves existing agent settings and
attachments, and does not give new Jira release rights to admin specialists. Guy retains his existing
release rights. Source documents cannot authorize writes, and personnel/financial privacy rules remain.

Drop Word `.docx` files or UTF-8 `.txt`, `.md`, `.vtt` and `.srt` files into
**`<setup>/transcript-inbox/`**. Text files are limited to 128 KiB. Word files can be up to 10 MiB,
with at most 8 MiB of document XML and 128 KiB of extracted text. Word paragraphs and tables are read
in document order; formatting runs, tabs and line breaks are preserved as text. Deleted tracked text
and field instructions are excluded. Images/OCR, headers, footers, comments and footnotes are not
ingested. Legacy `.doc`, encrypted documents, audio, PDFs and recursive directories are unsupported.
No Office application, macro execution or external-link fetching is involved. Copy through a temporary filename
and rename it when finished; the scanner also waits ten seconds after the last modification and rejects
symlinks and Office lock files. Publish finished files by renaming them into the inbox; do not edit
them in place after dropping them. The worker atomically claims each file in a private `.ingesting/`
subdirectory, saves its extracted text to the durable queue, then deletes the source file. Deletion
does not wait for model analysis or action approval; extracted text and action records remain saved.
Identical extracted text, including a renamed Word document or matching text file, is processed once;
the duplicate source is also removed after the existing saved text is confirmed. Invalid inputs,
queue backpressure and failed saves retain their source for retry. Interrupted claims recover after
restart. Failed or changed inputs return to the inbox without overwriting newer uploads; when a
newer file already uses that name, the original remains in `.ingesting/` and its path appears in status.
Files over the limit or invalid UTF-8 appear in the status heartbeat. A bounded durable queue holds 100
pending transcripts and at most 2,000 unresolved proposals; additional files wait in the inbox.

A dedicated Opus 5.5/high intake agent extracts at most 20 action proposals per transcript, with exact
source quotations, draft content, owners and resume conditions. It has no attached MCP tools and is
instructed to perform extraction only. The service creates unassigned review tickets in the private
**Transcript Actions** project. The Internal Admin project also receives the new status vocabulary;
its existing issues keep their statuses and no automatic workflow handlers are introduced.

| State | Meaning | How to continue |
| --- | --- | --- |
| Awaiting approval | The destination and proposed action/content are ready. | Review the ticket and copy its `/action approve <digest>` command into a new comment. |
| Blocked | A question, connection, access, dependency, timing constraint or execution uncertainty prevents completion. | Resolve the named prerequisite; update the proposal JSON and clear `blocker` for a proposal that has not run. |
| In progress | The service has frozen the approved snapshot and dispatched it. | Await the execution receipt. |
| Done | The execution agent reported a verified result URL. | Inspect the receipt and resulting artifact. |

Each external action needs its own explicit approval from the installation owner. Agent comments,
transcript text, `/decisions auto`, batch consent and status changes cannot approve it. Editing the
proposal invalidates old approval commands. `/action reject` withdraws an approval that has not been
claimed. Once claimed, the snapshot is frozen; editing or rejecting the review does not cancel an
already dispatched task. Stop that task through Multica if necessary.

The service enforces approval before dispatch. The execution agent must stay within the approved
scope and use normal connected tools; this is not a new permission boundary inside the model runtime.
Jira and Confluence actions run through `sdm-manager`. Creating a ticket never authorizes development,
and approval of a document never authorizes an email or release. Jira linking still precedes development;
private capture/review does not automatically create an external Jira issue before approval.

Email sending is not currently connected. Email proposals retain their drafts and are **Blocked** with
that reason; the service never invents a send receipt. Unsupported action kinds similarly remain blocked.
The worker dispatches one extraction and one approved action at a time using native Autopilots with
stable idempotency keys. Database constraints and a process lock coordinate worker overlap. Unknown
issue-create outcomes are reconciled by source marker, never blindly recreated. External writes remain
subject to provider concurrency and the execution agent's verified readback; exactly-once external
side effects are not promised.

After an execution fails or becomes uncertain, it is not automatically dispatched again. Inspect the
run and external records first, correct the proposal if necessary, then explicitly create a fresh review:

```bash
bash setup-transcripts.sh replan ACTION_ID --reason 'Checked Jira: no issue was created; access is fixed.' \
  --deployment-dir /absolute/path/to/setup
```

The new ticket requires a new approval. Its old approval cannot authorize the retry. Status output
contains source/action IDs and run IDs. Provider failure or invalid extraction output is reported there;
no external action follows an invalid extraction. No mail, Jira issue or Confluence page is created by
installation. The inbox, transcript state and queue are ignored by Git and private to the installing
user. The sidecar has no Docker socket, uses 0.5 CPU/512 MiB, and persists across Docker restarts.
Use `setup-transcripts.sh stop` or `start` to control just this service. Existing runtimes stay running.
