import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const configFile = resolve(
  process.env.RELAY_CONFIG_FILE ?? join(root, "config.json"),
);
const secrets = join(dirname(configFile), ".secrets");
const stateFile = join(secrets, "multica-state.json");
const lock = join(secrets, ".multica-lock");
const apply = process.argv.includes("--apply");
let locked = false;
let pendingFile;

function fail(message) {
  throw new Error(message);
}
function readJson(file, fallback) {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT" && fallback !== undefined) return fallback;
    fail(
      "Cannot read configuration or registration state. Check file format and permissions.",
    );
  }
}
function multica(args, input) {
  let output;
  try {
    output = execFileSync(
      "multica",
      ["--profile", process.env.MULTICA_PROFILE, "workspace", "mcp", ...args],
      {
        input,
        encoding: "utf8",
        stdio: ["pipe", "pipe", "pipe"],
        timeout: 30000,
        maxBuffer: 1048576,
      },
    );
  } catch {
    fail(
      `Multica ${args[0]} failed. Check the profile, login and workspace permissions. Check the library before retrying a failed write.`,
    );
  }
  try {
    return JSON.parse(output);
  } catch {
    fail(
      "Multica returned an unexpected response. Check the library before retrying.",
    );
  }
}

try {
  if (process.argv.slice(2).some((arg) => arg !== "--apply"))
    fail("Use --apply to register endpoints; omit it for a preview.");
  const profile = process.env.MULTICA_PROFILE;
  const workspaceId = process.env.MULTICA_WORKSPACE_ID;
  if (!profile || !/^[a-f0-9-]{36}$/i.test(workspaceId ?? ""))
    fail("Set MULTICA_PROFILE and MULTICA_WORKSPACE_ID explicitly.");
  let base;
  try {
    base = new URL(process.env.RELAY_BASE_URL ?? "http://127.0.0.1:8090");
  } catch {
    fail("RELAY_BASE_URL must be an HTTP(S) origin.");
  }
  if (
    !["http:", "https:"].includes(base.protocol) ||
    base.username ||
    base.password ||
    base.search ||
    base.hash ||
    base.pathname !== "/"
  )
    fail(
      "RELAY_BASE_URL must be an HTTP(S) origin without credentials or a path.",
    );
  const config = readJson(configFile);
  const endpoints = config.http?.endpoints;
  if (!Array.isArray(endpoints) || !endpoints.length)
    fail("Configure scoped http.endpoints first.");
  const seenTokens = new Set();
  const entries = endpoints.map((endpoint) => {
    if (
      !/^[a-z][a-z0-9_]{0,39}$/.test(endpoint.name) ||
      !/^\/mcp\/[a-z0-9][a-z0-9._-]*(?:\/[a-z0-9][a-z0-9._-]*)*$/.test(
        endpoint.path,
      ) ||
      !Array.isArray(endpoint.allowTools) ||
      !endpoint.allowTools.length
    )
      fail("Invalid scoped endpoint configuration.");
    const filename = /^\/run\/relay-secrets\/([a-z][a-z0-9-]{0,79})$/.exec(
      endpoint.token?.file ?? "",
    );
    let token;
    try {
      token = (
        filename
          ? readFileSync(join(secrets, filename[1]), "utf8")
          : process.env[endpoint.token?.env]
      )?.trim();
    } catch {
      fail("Cannot read an endpoint token. Run npm run setup first.");
    }
    if (!token || token.length < 32 || seenTokens.has(token))
      fail("Endpoint tokens must be distinct and at least 32 characters long.");
    seenTokens.add(token);
    return {
      name: `factory-${endpoint.name.replaceAll("_", "-")}`,
      spec: {
        type: "http",
        url: base.origin + endpoint.path,
        headers: { Authorization: `Bearer ${token}` },
      },
    };
  });
  if (
    new Set(entries.map((entry) => entry.name)).size !== entries.length ||
    new Set(entries.map((entry) => entry.spec.url)).size !== entries.length
  )
    fail("Endpoint names and paths must be unique.");
  mkdirSync(secrets, { recursive: true, mode: 0o700 });
  try {
    mkdirSync(lock, { mode: 0o700 });
    locked = true;
  } catch {
    fail(
      "Multica sync is locked. Check for a running sync before removing .secrets/.multica-lock.",
    );
  }
  const state = readJson(stateFile, { profile, workspaceId, servers: {} });
  if (
    state.profile !== profile ||
    state.workspaceId !== workspaceId ||
    !state.servers ||
    typeof state.servers !== "object"
  )
    fail(
      "Registration state belongs to a different profile or workspace, or is invalid.",
    );
  const existing = multica(["list", workspaceId, "--output", "json"]);
  if (!Array.isArray(existing))
    fail("Multica returned an unexpected server list.");
  for (const entry of entries) {
    const id = state.servers[entry.name];
    if (id && !existing.some((server) => server.id === id))
      fail(
        "A tracked MCP server is missing. Reconcile .secrets/multica-state.json before syncing.",
      );
    if (
      existing.some((server) => server.name === entry.name && server.id !== id)
    )
      fail(
        "A server name is already used by an untracked entry. Rename or reconcile it before syncing.",
      );
  }
  for (const entry of entries) {
    const id = state.servers[entry.name];
    if (!apply) {
      console.log(
        `${id ? "Update" : "Create"} ${entry.name}: ${entry.spec.url}`,
      );
      continue;
    }
    multica(
      id
        ? [
            "update",
            id,
            workspaceId,
            "--name",
            entry.name,
            "--server-config-stdin",
            "--output",
            "json",
          ]
        : [
            "add",
            entry.name,
            workspaceId,
            "--server-config-stdin",
            "--output",
            "json",
          ],
      JSON.stringify(entry.spec),
    );
    const registered = multica(["list", workspaceId, "--output", "json"]);
    const matches = Array.isArray(registered)
      ? registered.filter(
          (server) => server.name === entry.name && (!id || server.id === id),
        )
      : [];
    const serverId = matches.length === 1 ? matches[0].id : undefined;
    if (typeof serverId !== "string" || !/^[a-f0-9-]{36}$/i.test(serverId))
      fail(
        "Could not verify the registered server ID. Inspect the library before retrying.",
      );
    state.servers[entry.name] = serverId;
    pendingFile = join(secrets, `.multica-state-${randomUUID()}`);
    writeFileSync(pendingFile, JSON.stringify(state, null, 2) + "\n", {
      flag: "wx",
      mode: 0o600,
    });
    renameSync(pendingFile, stateFile);
    pendingFile = undefined;
    console.log(`${id ? "Updated" : "Created"} ${entry.name}`);
  }
  if (apply) {
    const current = multica(["list", workspaceId, "--output", "json"]);
    if (
      !Array.isArray(current) ||
      entries.some(
        (entry) =>
          !current.some(
            (server) =>
              server.id === state.servers[entry.name] &&
              server.name === entry.name,
          ),
      )
    )
      fail(
        "Registration verification failed. Inspect the workspace MCP library.",
      );
    console.log(
      `Verified ${entries.length} workspace MCP servers. No agent assignments were changed.`,
    );
  } else
    console.log("Preview only. Re-run with --apply to register these servers.");
} catch (error) {
  console.error(
    error instanceof Error ? error.message : "Multica sync failed.",
  );
  process.exitCode = 1;
} finally {
  if (pendingFile) rmSync(pendingFile, { force: true });
  if (locked) rmSync(lock, { recursive: true, force: true });
}
