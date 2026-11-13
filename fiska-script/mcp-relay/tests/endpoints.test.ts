import { test } from "node:test";
import assert from "node:assert/strict";
import { z } from "zod";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { configSchema } from "../src/config.js";
import { createHttpServer } from "../src/server.js";
import { ToolRegistry } from "../src/tools.js";

const endpoints = [
  {
    name: "jira",
    path: "/mcp/jira",
    token: { env: "ENDPOINT_JIRA" },
    allowTools: ["jira__request"],
  },
  {
    name: "confluence",
    path: "/mcp/confluence",
    token: { env: "ENDPOINT_CONFLUENCE" },
    allowTools: ["confluence__request"],
  },
];

test("endpoint configuration rejects ambiguous routes, missing authentication and reused credentials", () => {
  for (const http of [
    {},
    { endpoints: [endpoints[0], endpoints[0]] },
    { endpoints: [endpoints[0], { ...endpoints[1], name: "jira" }] },
    { endpoints: [endpoints[0], { ...endpoints[1], path: "/mcp/jira" }] },
    { endpoints: [{ ...endpoints[0], path: "/mcp" }] },
    { endpoints: [{ ...endpoints[0], path: "/mcp/../jira" }] },
    { endpoints: [{ ...endpoints[0], allowTools: [] }] },
    { endpoints: [{ ...endpoints[0], allowTools: ["*"] }] },
  ])
    assert.equal(configSchema.safeParse({ http }).success, false);
  process.env.ENDPOINT_JIRA = "x".repeat(64);
  process.env.ENDPOINT_CONFLUENCE = process.env.ENDPOINT_JIRA;
  try {
    assert.throws(
      () =>
        createHttpServer(
          configSchema.parse({ http: { endpoints } }).http,
          new ToolRegistry(),
        ),
      /different token/,
    );
    process.env.ENDPOINT_CONFLUENCE = "short";
    assert.throws(
      () =>
        createHttpServer(
          configSchema.parse({ http: { endpoints } }).http,
          new ToolRegistry(),
        ),
      /32 characters/,
    );
  } finally {
    delete process.env.ENDPOINT_JIRA;
    delete process.env.ENDPOINT_CONFLUENCE;
  }
});

test("MCP endpoints isolate discovery, execution, credentials and concurrent requests", async (t) => {
  process.env.ENDPOINT_JIRA = "j".repeat(64);
  process.env.ENDPOINT_CONFLUENCE = "c".repeat(64);
  process.env.ENDPOINT_ADMIN = "a".repeat(64);
  const registry = new ToolRegistry();
  let forbiddenCalls = 0;
  for (const name of ["jira__request", "confluence__request"]) {
    registry.local(name, name, z.object({}).strict(), true, async () => {
      await new Promise((resolve) => setImmediate(resolve));
      return { name };
    });
  }
  for (const name of ["relay__refresh", "mail__list", "command__danger"]) {
    registry.local(name, name, z.object({}).strict(), true, async () => {
      forbiddenCalls++;
      return {};
    });
  }
  const config = configSchema.parse({
    http: { endpoints, token: { env: "ENDPOINT_ADMIN" } },
  });
  const server = createHttpServer(config.http, registry);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const clients: Client[] = [];
  t.after(async () => {
    await Promise.all(clients.map((client) => client.close()));
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    delete process.env.ENDPOINT_JIRA;
    delete process.env.ENDPOINT_CONFLUENCE;
    delete process.env.ENDPOINT_ADMIN;
  });
  const routes = [
    ...endpoints,
    {
      name: "admin",
      path: "/mcp",
      token: { env: "ENDPOINT_ADMIN" },
      allowTools: registry.list().map((tool) => tool.name),
    },
  ];
  for (const route of routes) {
    assert.equal((await fetch(origin + route.path)).status, 401);
    for (const other of routes.filter((candidate) => candidate !== route)) {
      assert.equal(
        (
          await fetch(origin + route.path, {
            headers: {
              Authorization: `Bearer ${process.env[other.token.env]}`,
            },
          })
        ).status,
        401,
      );
    }
    const client = new Client({ name: route.name, version: "1" });
    clients.push(client);
    await client.connect(
      new StreamableHTTPClientTransport(new URL(origin + route.path), {
        requestInit: {
          headers: { Authorization: `Bearer ${process.env[route.token.env]}` },
        },
      }),
    );
    assert.deepEqual(
      (await client.listTools()).tools.map((tool) => tool.name),
      route.allowTools,
    );
    if (route.name === "admin") continue;
    for (const name of registry
      .list()
      .map((tool) => tool.name)
      .filter((name) => !route.allowTools.includes(name))) {
      assert.equal(
        (await client.callTool({ name, arguments: {} })).isError,
        true,
      );
    }
  }
  for (const path of [
    "/mcp/jira/",
    "/mcp/jira?other=confluence",
    "/mcp/%6aira",
    "/mcp/jira/confluence",
    "/mcp/unknown",
  ]) {
    assert.equal(
      (
        await fetch(origin + path, {
          headers: { Authorization: `Bearer ${process.env.ENDPOINT_JIRA}` },
        })
      ).status,
      404,
    );
  }
  const answers = await Promise.all(
    Array.from({ length: 16 }, (_, index) => {
      const i = index % 2;
      return clients[i].callTool({
        name: endpoints[i].allowTools[0],
        arguments: {},
      });
    }),
  );
  answers.forEach((answer, index) => {
    assert.equal(answer.isError, undefined);
    assert.deepEqual(answer.content, [
      {
        type: "text",
        text: JSON.stringify({ name: endpoints[index % 2].allowTools[0] }),
      },
    ]);
  });
  registry.local(
    "jira__new_tool",
    "new",
    z.object({}).strict(),
    true,
    async () => {
      forbiddenCalls++;
      return {};
    },
  );
  assert.deepEqual(
    (await clients[0].listTools()).tools.map((tool) => tool.name),
    ["jira__request"],
  );
  assert.equal(
    (await clients[0].callTool({ name: "jira__new_tool", arguments: {} }))
      .isError,
    true,
  );
  assert.equal(forbiddenCalls, 0);
  const oldToken = process.env.ENDPOINT_JIRA;
  process.env.ENDPOINT_JIRA = "r".repeat(64);
  assert.equal(
    (
      await fetch(origin + "/mcp/jira", {
        headers: { Authorization: `Bearer ${oldToken}` },
      })
    ).status,
    401,
  );
  assert.equal(
    (
      await fetch(origin + "/mcp/jira", {
        headers: { Authorization: `Bearer ${process.env.ENDPOINT_JIRA}` },
      })
    ).status,
    405,
  );
  delete process.env.ENDPOINT_JIRA;
  assert.equal((await fetch(origin + "/mcp/jira")).status, 503);
  assert.equal((await clients[1].listTools()).tools.length, 1);
});

test("aggregate endpoint is absent when its token is omitted", async (t) => {
  process.env.ENDPOINT_JIRA = "j".repeat(64);
  const server = createHttpServer(
    configSchema.parse({ http: { endpoints: [endpoints[0]] } }).http,
    new ToolRegistry(),
  );
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    delete process.env.ENDPOINT_JIRA;
  });
  const origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  assert.equal((await fetch(origin + "/mcp")).status, 404);
  assert.equal((await fetch(origin + "/healthz")).status, 200);
});
