import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const run = promisify(execFile);
const script = fileURLToPath(
  new URL("../scripts/multica-assign.mjs", import.meta.url),
);

test("agent assignment enables only planned servers, preserves existing access and is repeatable", async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "relay-assignment-test-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const bin = join(directory, "bin");
  mkdirSync(bin);
  mkdirSync(join(directory, ".secrets"));
  const workspaceId = "00000000-0000-4000-8000-000000000001";
  writeFileSync(
    join(directory, ".secrets/multica-state.json"),
    JSON.stringify({
      profile: "test",
      workspaceId,
      servers: { "factory-jira": "jira", "factory-confluence": "confluence" },
    }),
  );
  const policyFile = join(directory, "policy.json");
  const policy = {
    agents: {
      planner: {
        servers: ["factory-jira", "factory-confluence"],
        purpose: "Read requirements.",
      },
      database: {
        servers: [] as string[],
        purpose: "Dedicated database broker is unavailable.",
      },
    },
  };
  writeFileSync(policyFile, JSON.stringify(policy));
  const database = join(directory, "remote.json");
  const initial = {
    agents: [
      { id: "planner", name: "planner", workspace_id: workspaceId },
      { id: "database", name: "database", workspace_id: workspaceId },
    ],
    servers: [
      { id: "jira", name: "factory-jira" },
      { id: "confluence", name: "factory-confluence" },
    ],
    bindings: {
      planner: [
        { id: "existing", name: "existing", enabled: true },
        { id: "jira", name: "factory-jira", enabled: false },
      ],
      database: [],
    },
    mutations: 0,
  };
  writeFileSync(database, JSON.stringify(initial));
  writeFileSync(
    join(bin, "multica"),
    `#!${process.execPath}
const fs = require("node:fs");
const args = process.argv.slice(4);
const path = process.env.FAKE_DATABASE;
const data = JSON.parse(fs.readFileSync(path, "utf8"));
if (args[0] === "workspace") console.log(JSON.stringify(data.servers));
else if (args[1] === "list") console.log(JSON.stringify(data.agents));
else if (args[1] === "mcp") {
  const action = args[2], agent = args[3], server = args[4];
  if (action === "list") { console.log(JSON.stringify(data.bindings[agent])); process.exit(0); }
  let binding = data.bindings[agent].find(item => item.id === server);
  if (action === "add" && !binding) { binding = { ...data.servers.find(item => item.id === server), enabled: true }; data.bindings[agent].push(binding); }
  else if (action === "enable" && binding) binding.enabled = true;
  else process.exit(1);
  data.mutations++;
  fs.writeFileSync(path, JSON.stringify(data));
  console.log(JSON.stringify(data.bindings[agent]));
} else process.exit(1);
`,
    { mode: 0o700 },
  );
  const env = {
    ...process.env,
    PATH: `${bin}:${process.env.PATH}`,
    MULTICA_PROFILE: "test",
    MULTICA_WORKSPACE_ID: workspaceId,
    RELAY_CONFIG_FILE: join(directory, "config.json"),
    RELAY_ASSIGNMENTS_FILE: policyFile,
    FAKE_DATABASE: database,
  };
  const preview = await run(process.execPath, [script], { env });
  assert.match(preview.stdout, /Preview: 2 additions/);
  assert.deepEqual(JSON.parse(readFileSync(database, "utf8")), initial);
  const first = await run(process.execPath, [script, "--apply"], { env });
  assert.match(
    first.stdout,
    /Verified 2 enabled bindings across 1 agents; 2 bindings/,
  );
  const after = JSON.parse(readFileSync(database, "utf8"));
  assert.equal(after.mutations, 2);
  assert.deepEqual(after.bindings.planner, [
    { id: "existing", name: "existing", enabled: true },
    { id: "jira", name: "factory-jira", enabled: true },
    { id: "confluence", name: "factory-confluence", enabled: true },
  ]);
  assert.deepEqual(after.bindings.database, []);
  const second = await run(process.execPath, [script, "--apply"], { env });
  assert.match(second.stdout, /0 bindings added or enabled/);
  assert.deepEqual(JSON.parse(readFileSync(database, "utf8")), after);
  policy.agents.database.servers = ["unregistered"];
  writeFileSync(policyFile, JSON.stringify(policy));
  await assert.rejects(
    run(process.execPath, [script, "--apply"], { env }),
    (error: any) => {
      assert.match(error.stderr, /not registered. No assignments were changed/);
      return true;
    },
  );
  assert.deepEqual(JSON.parse(readFileSync(database, "utf8")), after);
});
