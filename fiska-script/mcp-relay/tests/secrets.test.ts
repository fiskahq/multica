import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const run = promisify(execFile);
const script = fileURLToPath(
  new URL("../scripts/sync-secrets.mjs", import.meta.url),
);

test("secret sync batches 1Password access, protects values and preserves credentials on failure", async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "relay-secret-test-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const bin = join(directory, "bin");
  mkdirSync(bin);
  const secrets = join(directory, "secrets");
  mkdirSync(secrets, { mode: 0o700 });
  const manifest = join(secrets, "bindings.json");
  writeFileSync(
    manifest,
    JSON.stringify({
      first: "op://vault/item/first",
      second: "op://vault/item/second",
    }),
  );
  writeFileSync(
    join(bin, "op"),
    `#!${process.execPath}
const { readFileSync, appendFileSync } = require("node:fs");
const { spawnSync } = require("node:child_process");
const args = process.argv.slice(2);
if (args[0] !== "run" || args[1] !== "--env-file" || args[3] !== "--") process.exit(2);
appendFileSync(process.env.FAKE_OP_CALLS, "call\\n");
if (process.env.FAKE_OP_FAIL) { console.error("private-test-secret"); process.exit(1); }
const env = { ...process.env };
for (const line of readFileSync(args[2], "utf8").trim().split("\\n")) {
  const index = line.indexOf("=");
  const ref = JSON.parse(line.slice(index + 1));
  env[line.slice(0, index)] = "private-test-secret-" + ref.split("/").at(-1);
}
const child = spawnSync(args[4], args.slice(5), { env, stdio: "inherit" });
process.exit(child.status ?? 1);
`,
    { mode: 0o700 },
  );
  const calls = join(directory, "calls");
  const env = {
    ...process.env,
    PATH: `${bin}:${process.env.PATH}`,
    FAKE_OP_CALLS: calls,
  };
  const result = await run(process.execPath, [script, manifest], { env });
  assert.match(result.stdout, /Updated 2 credential files/);
  assert.doesNotMatch(result.stdout + result.stderr, /private-test-secret/);
  assert.equal(readFileSync(calls, "utf8"), "call\n");
  assert.equal(
    readFileSync(join(secrets, "first"), "utf8"),
    "private-test-secret-first",
  );
  assert.equal(statSync(join(secrets, "first")).mode & 0o777, 0o600);
  await assert.rejects(
    run(process.execPath, [script, manifest], {
      env: { ...env, FAKE_OP_FAIL: "1" },
    }),
    (error: any) => {
      assert.match(error.stderr, /Existing files were preserved/);
      assert.doesNotMatch(error.stdout + error.stderr, /private-test-secret/);
      return true;
    },
  );
  assert.equal(
    readFileSync(join(secrets, "first"), "utf8"),
    "private-test-secret-first",
  );
  assert.deepEqual(readdirSync(secrets).sort(), [
    "bindings.json",
    "first",
    "second",
  ]);
  mkdirSync(join(secrets, ".sync-lock"));
  await assert.rejects(
    run(process.execPath, [script, manifest], { env }),
    (error: any) => {
      assert.match(error.stderr, /Secret sync is locked/);
      return true;
    },
  );
  assert.ok(statSync(join(secrets, ".sync-lock")).isDirectory());
  assert.equal(readFileSync(calls, "utf8"), "call\ncall\n");
});
