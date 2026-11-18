import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { configSchema } from "../src/config.js";
import { registerServices } from "../src/services.js";
import { ToolRegistry } from "../src/tools.js";

const example = JSON.parse(
  readFileSync(
    new URL("../examples/jira-release.json", import.meta.url),
    "utf8",
  ),
);

test("release tool permits scoped version changes and rejects deletion, merging and ticket writes before fetch", async (t) => {
  process.env.TRIAGE_TEST_TOKEN = "fake-token";
  const calls: string[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (url, init) => {
    calls.push(`${init!.method} ${new URL(String(url)).pathname}`);
    return new Response('{"key":"MS-123"}', {
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = original;
    delete process.env.TRIAGE_TEST_TOKEN;
  });
  const integration = {
    ...example.integration,
    auth: { type: "bearer", token: { env: "TRIAGE_TEST_TOKEN" } },
  };
  const config = configSchema.parse({
    http: { token: { env: "TRIAGE_TEST_TOKEN" } },
    rest: [integration],
    jira: {
      site: "https://example.atlassian.net",
      email: { env: "TRIAGE_TEST_TOKEN" },
      token: { env: "TRIAGE_TEST_TOKEN" },
    },
  });
  const registry = new ToolRegistry();
  registerServices(registry, config);
  const allowed = [
    ["POST", "/rest/api/3/version"],
    ["PUT", "/rest/api/3/version/12345"],
    ["POST", "/rest/api/3/version/12345/move"],
    ["GET", "/rest/api/3/project/MS/version"],
    ["GET", "/rest/api/3/version/12345/relatedIssueCounts"],
  ];
  for (const [method, path] of allowed)
    assert.ok(
      !(
        await registry.call("jira_release__request", { method, path, body: {} })
      ).isError,
      `${method} ${path}`,
    );
  assert.equal(calls.length, allowed.length);
  const denied = [
    ["DELETE", "/rest/api/3/version/12345"],
    ["PUT", "/rest/api/3/version/12345/mergeto/54321"],
    ["POST", "/rest/api/3/version/12345/removeAndSwap"],
    ["POST", "/rest/api/3/project"],
    ["POST", "/rest/api/3/issue"],
    ["PUT", "/rest/api/3/issue/MS-123"],
    ["PUT", "/rest/api/3/version/12345/../../project"],
    ["PUT", "/rest/api/3/version/12345\n"],
  ];
  for (const [method, path] of denied)
    assert.equal(
      (await registry.call("jira_release__request", { method, path, body: {} }))
        .isError,
      true,
      `${method} ${path}`,
    );
  assert.equal(
    (
      await registry.call("jira__request", {
        method: "POST",
        path: "/rest/api/3/issue",
        body: {},
      })
    ).isError,
    true,
  );
  assert.equal(
    calls.length,
    allowed.length,
    "Denied calls must never reach the provider",
  );
});
