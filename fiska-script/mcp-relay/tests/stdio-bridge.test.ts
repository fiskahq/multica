import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { z } from "zod";
import { configSchema } from "../src/config.js";
import { ToolRegistry } from "../src/tools.js";
import { createHttpServer } from "../src/server.js";

const adapter = fileURLToPath(new URL("../../relay-stdio.py", import.meta.url));
const token = "test-endpoint-secret-".repeat(3);

function transport(url: string, auth = token) {
  return new StdioClientTransport({
    command: "python3",
    args: [adapter],
    stderr: "pipe",
    env: {
      MULTICA_RELAY_URL: url,
      MULTICA_RELAY_HEADERS: JSON.stringify({
        Authorization: `Bearer ${auth}`,
      }),
    },
  });
}

test("Kiro-compatible stdio preserves scoped relay tools, arguments and write results", async () => {
  process.env.BRIDGE_TEST_TOKEN = token;
  const registry = new ToolRegistry();
  let writes = 0;
  registry.local(
    "jira_triage__request",
    "test write",
    z.object({ body: z.string() }),
    false,
    async ({ body }) => {
      writes++;
      return { saved: body };
    },
  );
  registry.local(
    "other__private",
    "not granted",
    z.object({}),
    true,
    async () => ({}),
  );
  const server = createHttpServer(
    configSchema.parse({
      http: {
        endpoints: [
          {
            name: "jira_triage",
            path: "/mcp/jira-triage",
            token: { env: "BRIDGE_TEST_TOKEN" },
            allowTools: ["jira_triage__request"],
          },
        ],
      },
    }).http,
    registry,
  );
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as { port: number };
  const client = new Client({ name: "stdio-test", version: "1" });
  const wire = transport(`http://127.0.0.1:${address.port}/mcp/jira-triage`);
  try {
    await client.connect(wire);
    assert.deepEqual(
      (await client.listTools()).tools.map((t) => t.name),
      ["jira_triage__request"],
    );
    const result = await client.callTool({
      name: "jira_triage__request",
      arguments: { body: "PR, impact and validation" },
    });
    assert.ok(!result.isError);
    assert.match(JSON.stringify(result), /PR, impact and validation/);
    assert.equal(writes, 1);
    assert.equal(
      (await client.callTool({ name: "other__private", arguments: {} }))
        .isError,
      true,
    );
  } finally {
    await client.close();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    delete process.env.BRIDGE_TEST_TOKEN;
  }
});

test("uncertain HTTP failures are sanitized and never retried", async () => {
  let calls = 0;
  const server = createServer((_req, res) => {
    calls++;
    res.writeHead(503);
    res.end(token);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as { port: number };
  const client = new Client({ name: "stdio-test", version: "1" });
  try {
    await assert.rejects(
      client.connect(transport(`http://127.0.0.1:${address.port}/mcp`)),
      (error) => {
        assert.match(String(error), /HTTP 503/);
        assert.match(String(error), /read back before retrying/);
        assert.ok(!String(error).includes(token));
        return true;
      },
    );
    assert.equal(calls, 1);
  } finally {
    await client.close();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
