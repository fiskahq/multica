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
  new URL("../scripts/multica-sync.mjs", import.meta.url),
);

test("Multica sync sends scoped tokens on stdin, updates owned entries and preserves unrelated servers", async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "relay-multica-test-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const bin = join(directory, "bin");
  mkdirSync(bin);
  mkdirSync(join(directory, ".secrets"), { mode: 0o700 });
  const token = "sensitive-endpoint-token-".repeat(3);
  writeFileSync(join(directory, ".secrets/endpoint-jira-token"), token, {
    mode: 0o600,
  });
  const configFile = join(directory, "config.json");
  const config = {
    http: {
      endpoints: [
        {
          name: "jira",
          path: "/mcp/jira",
          token: { file: "/run/relay-secrets/endpoint-jira-token" },
          allowTools: ["jira__request"],
        },
      ],
    },
  };
  writeFileSync(configFile, JSON.stringify(config));
  const database = join(directory, "multica.json");
  const foreign = {
    id: "00000000-0000-4000-8000-000000000001",
    name: "existing",
    transport: "http",
  };
  writeFileSync(database, JSON.stringify([foreign]));
  const calls = join(directory, "calls.jsonl");
  writeFileSync(
    join(bin, "multica"),
    `#!${process.execPath}
const fs = require("node:fs");
const args = process.argv.slice(2);
const action = args[4];
fs.appendFileSync(process.env.FAKE_CALLS, JSON.stringify(args) + "\\n");
if (process.env.FAKE_FAILURE) { console.error("sensitive-endpoint-token"); process.exit(1); }
const servers = JSON.parse(fs.readFileSync(process.env.FAKE_DATABASE, "utf8"));
if (action === "list") { console.log(JSON.stringify(servers.map(({ id, name, transport }) => ({ id, name, transport })))); process.exit(0); }
if (!args.includes("--server-config-stdin")) process.exit(1);
const spec = JSON.parse(fs.readFileSync(0, "utf8"));
let server;
if (action === "add") { server = { id: require("node:crypto").randomUUID(), name: args[5], transport: "http", spec }; servers.push(server); }
else if (action === "update") { server = servers.find(s => s.id === args[5]); if (!server) process.exit(1); server.spec = spec; server.name = args[args.indexOf("--name") + 1]; }
else process.exit(1);
fs.writeFileSync(process.env.FAKE_DATABASE, JSON.stringify(servers));
console.log(JSON.stringify({ data: { id: server.id, name: server.name, transport: server.transport } }));
`,
    { mode: 0o700 },
  );
  const env = {
    ...process.env,
    PATH: `${bin}:${process.env.PATH}`,
    MULTICA_PROFILE: "test",
    MULTICA_WORKSPACE_ID: "00000000-0000-4000-8000-000000000002",
    RELAY_BASE_URL: "http://127.0.0.1:8090",
    RELAY_CONFIG_FILE: configFile,
    FAKE_DATABASE: database,
    FAKE_CALLS: calls,
  };
  const preview = await run(process.execPath, [script], { env });
  assert.match(preview.stdout, /Preview only/);
  assert.deepEqual(JSON.parse(readFileSync(database, "utf8")), [foreign]);
  const first = await run(process.execPath, [script, "--apply"], { env });
  assert.match(first.stdout, /Created factory-jira/);
  const stateFile = join(directory, ".secrets/multica-state.json");
  const firstState = readFileSync(stateFile, "utf8");
  const second = await run(process.execPath, [script, "--apply"], { env });
  assert.match(second.stdout, /Updated factory-jira/);
  assert.equal(readFileSync(stateFile, "utf8"), firstState);
  const servers = JSON.parse(readFileSync(database, "utf8"));
  assert.equal(servers.length, 2);
  assert.deepEqual(servers[0], foreign);
  assert.deepEqual(servers[1].spec, {
    type: "http",
    url: "http://127.0.0.1:8090/mcp/jira",
    headers: { Authorization: `Bearer ${token}` },
  });
  assert.doesNotMatch(
    first.stdout +
      first.stderr +
      second.stdout +
      firstState +
      readFileSync(calls, "utf8"),
    /sensitive-endpoint-token/,
  );
  rmSync(stateFile);
  await assert.rejects(
    run(process.execPath, [script, "--apply"], { env }),
    (error: any) => {
      assert.match(error.stderr, /untracked entry/);
      return true;
    },
  );
  assert.equal(JSON.parse(readFileSync(database, "utf8")).length, 2);
  await assert.rejects(
    run(process.execPath, [script, "--apply"], {
      env: { ...env, FAKE_FAILURE: "1" },
    }),
    (error: any) => {
      assert.match(error.stderr, /Multica list failed/);
      assert.doesNotMatch(
        error.stderr + error.stdout,
        /sensitive-endpoint-token/,
      );
      return true;
    },
  );
});
