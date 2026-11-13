import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { configSchema } from "../src/config.js";
import { registerServices } from "../src/services.js";
import { ToolRegistry } from "../src/tools.js";

const example = JSON.parse(
  readFileSync(
    new URL("../examples/jira-triage.json", import.meta.url),
    "utf8",
  ),
);

test("triage permits individual ticket work and rejects destructive or unrelated writes before fetch", async (t) => {
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
    ["POST", "/rest/api/3/issue"],
    ["PUT", "/rest/api/3/issue/MS-123"],
    ["PUT", "/rest/api/3/issue/MS-123/assignee"],
    ["POST", "/rest/api/3/issue/MS-123/comment"],
    ["POST", "/rest/api/3/issue/MS-123/transitions"],
    ["POST", "/rest/api/3/issueLink"],
    ["GET", "/rest/api/3/issue/MS-123"],
  ];
  for (const [method, path] of allowed)
    assert.ok(
      !(await registry.call("jira_triage__request", { method, path, body: {} }))
        .isError,
      `${method} ${path}`,
    );
  assert.equal(calls.length, allowed.length);
  const denied = [
    ["DELETE", "/rest/api/3/issue/MS-123"],
    ["DELETE", "/rest/api/3/issueLink/123"],
    ["POST", "/rest/api/3/issue/bulk"],
    ["PUT", "/rest/api/3/issue/archive"],
    ["POST", "/rest/api/3/issue/MS-123/notify"],
    ["POST", "/rest/api/3/project"],
    ["PATCH", "/rest/api/3/issue/MS-123"],
    ["PUT", "/rest/api/3/issue/MS-123/"],
    ["PUT", "/rest/api/3/issue/MS-123/../../project"],
    ["PUT", "/rest/api/3/issue/MS-123\n"],
  ];
  for (const [method, path] of denied)
    assert.equal(
      (await registry.call("jira_triage__request", { method, path, body: {} }))
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

test("write route configuration rejects invalid and unanchored patterns", () => {
  const http = { token: { env: "TRIAGE_TEST_TOKEN" } };
  assert.doesNotThrow(() =>
    configSchema.parse({ http, rest: [example.integration] }),
  );
  for (const pathPattern of ["/issue", "^[broken$", "^/issue", "/issue$"])
    assert.throws(() =>
      configSchema.parse({
        http,
        rest: [
          {
            ...example.integration,
            writeRules: [{ method: "POST", pathPattern }],
          },
        ],
      }),
    );
});

test("empty write rules and allowWrites false independently deny writes", async () => {
  for (const overrides of [{ writeRules: [] }, { allowWrites: false }]) {
    const registry = new ToolRegistry();
    registerServices(
      registry,
      configSchema.parse({
        http: { token: { env: "TRIAGE_TEST_TOKEN" } },
        rest: [{ ...example.integration, ...overrides }],
      }),
    );
    const result = await registry.call("jira_triage__request", {
      method: "POST",
      path: "/rest/api/3/issue",
      body: {},
    });
    assert.equal(result.isError, true);
    assert.match(
      JSON.stringify(result),
      /Write route is not allowed|Writes are disabled/,
    );
  }
});
