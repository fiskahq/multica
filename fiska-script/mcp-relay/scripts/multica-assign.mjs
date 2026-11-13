import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const configFile = resolve(
  process.env.RELAY_CONFIG_FILE ?? join(root, "config.json"),
);
const secrets = join(dirname(configFile), ".secrets");
const policyFile = resolve(
  process.env.RELAY_ASSIGNMENTS_FILE ?? join(root, "examples/agent-mcp.json"),
);
const lock = join(secrets, ".multica-lock");
const apply = process.argv.includes("--apply");
let locked = false;

function fail(message) {
  throw new Error(message);
}
function readJson(file) {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    fail(
      "Cannot read MCP registration state or assignment policy. Check file format and permissions.",
    );
  }
}

try {
  if (process.argv.slice(2).some((arg) => arg !== "--apply"))
    fail("Use --apply to assign servers; omit it for a preview.");
  const state = readJson(join(secrets, "multica-state.json"));
  const policy = readJson(policyFile);
  if (
    !state.profile ||
    !/^[a-f0-9-]{36}$/i.test(state.workspaceId ?? "") ||
    !state.servers ||
    typeof state.servers !== "object"
  )
    fail("Invalid MCP registration state. Register the servers first.");
  if (
    (process.env.MULTICA_PROFILE &&
      process.env.MULTICA_PROFILE !== state.profile) ||
    (process.env.MULTICA_WORKSPACE_ID &&
      process.env.MULTICA_WORKSPACE_ID !== state.workspaceId)
  )
    fail(
      "The requested profile or workspace does not match the registered MCP servers.",
    );
  if (
    !policy.agents ||
    typeof policy.agents !== "object" ||
    Array.isArray(policy.agents) ||
    !Object.keys(policy.agents).length
  )
    fail("The assignment policy must name the agents to configure.");
  try {
    mkdirSync(lock, { mode: 0o700 });
    locked = true;
  } catch {
    fail(
      "Multica sync is locked. Check for another registration or assignment process before removing the lock.",
    );
  }
  const mc = (...args) => {
    let output;
    try {
      output = execFileSync(
        "multica",
        ["--profile", state.profile, ...args, "--output", "json"],
        {
          encoding: "utf8",
          stdio: ["ignore", "pipe", "pipe"],
          timeout: 30000,
          maxBuffer: 8388608,
          env: { ...process.env, MULTICA_WORKSPACE_ID: state.workspaceId },
        },
      );
    } catch {
      fail(
        `Multica ${args.slice(0, 3).join(" ")} failed. Check login and permissions; rerunning reconciles successful additions.`,
      );
    }
    try {
      return JSON.parse(output);
    } catch {
      fail(
        "Multica returned an unexpected response. Inspect assignments before retrying.",
      );
    }
  };
  const agents = mc("agent", "list");
  const servers = mc("workspace", "mcp", "list", state.workspaceId);
  if (!Array.isArray(agents) || !Array.isArray(servers))
    fail("Multica returned an unexpected agent or server list.");
  const plan = [];
  for (const [name, settings] of Object.entries(policy.agents)) {
    const matches = agents.filter(
      (agent) =>
        agent.name === name && agent.workspace_id === state.workspaceId,
    );
    if (matches.length !== 1)
      fail(
        `Expected one active agent named ${name} in the configured workspace.`,
      );
    if (
      !Array.isArray(settings.servers) ||
      settings.servers.some((server) => typeof server !== "string") ||
      new Set(settings.servers).size !== settings.servers.length ||
      typeof settings.purpose !== "string" ||
      !settings.purpose.trim()
    )
      fail(`Invalid assignment policy for ${name}.`);
    const agent = matches[0];
    const desired = settings.servers.map((serverName) => {
      const id = state.servers[serverName];
      if (
        !id ||
        !servers.some(
          (server) => server.id === id && server.name === serverName,
        )
      )
        fail(
          `MCP server ${serverName} is not registered. No assignments were changed.`,
        );
      return { id, name: serverName };
    });
    plan.push({ agent, desired });
  }
  for (const row of plan) {
    row.existing = mc("agent", "mcp", "list", row.agent.id);
    if (!Array.isArray(row.existing))
      fail("Multica returned an unexpected assignment list.");
  }
  let changed = 0;
  for (const { agent, desired, existing } of plan) {
    for (const server of desired) {
      const assigned = existing.find((item) => item.id === server.id);
      if (assigned?.enabled === true) continue;
      const operation = assigned ? "enable" : "add";
      console.log(
        `${apply ? operation : `Would ${operation}`} ${server.name} for ${agent.name}`,
      );
      if (apply) mc("agent", "mcp", operation, agent.id, server.id);
      changed++;
    }
  }
  if (apply) {
    for (const { agent, desired, existing } of plan) {
      const current = mc("agent", "mcp", "list", agent.id);
      if (
        !Array.isArray(current) ||
        desired.some(
          (server) =>
            !current.some(
              (entry) => entry.id === server.id && entry.enabled === true,
            ),
        )
      )
        fail(`Assignment verification failed for ${agent.name}.`);
      const desiredIds = new Set(desired.map((entry) => entry.id));
      if (
        existing.some(
          (before) =>
            !desiredIds.has(before.id) &&
            !current.some(
              (after) =>
                after.id === before.id && after.enabled === before.enabled,
            ),
        )
      )
        fail(
          `An unrelated assignment changed for ${agent.name}; inspect concurrent changes.`,
        );
    }
    console.log(
      `Verified ${plan.reduce((count, row) => count + row.desired.length, 0)} enabled bindings across ${plan.filter((row) => row.desired.length).length} agents; ${changed} bindings added or enabled. Existing unrelated assignments were preserved.`,
    );
  } else
    console.log(
      `Preview: ${changed} additions/enables. Re-run with --apply to assign these servers.`,
    );
  for (const { agent, desired } of plan.filter((row) => !row.desired.length))
    console.log(`${agent.name}: ${policy.agents[agent.name].purpose}`);
  for (const agent of agents.filter(
    (agent) => !Object.hasOwn(policy.agents, agent.name),
  ))
    console.log(
      `No policy for ${agent.name}; review its MCP needs separately.`,
    );
} catch (error) {
  console.error(
    error instanceof Error ? error.message : "MCP assignment failed.",
  );
  process.exitCode = 1;
} finally {
  if (locked) rmSync(lock, { recursive: true, force: true });
}
