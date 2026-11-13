import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { configSchema } from "../src/config.js";
import { ToolRegistry, result } from "../src/tools.js";
import { createHttpServer } from "../src/server.js";
import { Upstream } from "../src/upstreams.js";
import { registerCommands } from "../src/commands.js";

process.env.TEST_RELAY_TOKEN = "a".repeat(64);
const settings = () =>
  configSchema.parse({
    http: {
      host: "127.0.0.1",
      port: 0,
      token: { env: "TEST_RELAY_TOKEN" },
      maxBodyBytes: 2048,
    },
  });
async function listen(server: ReturnType<typeof createServer>) {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return new URL(
    `http://127.0.0.1:${(server.address() as { port: number }).port}/mcp`,
  );
}

test("MCP client initializes, lists and calls tools; authentication, origin and schema are enforced", async (t) => {
  const registry = new ToolRegistry();
  registry.local(
    "fixture__echo",
    "fixture",
    z.object({ text: z.string() }).strict(),
    true,
    async (args) => args,
  );
  const server = createHttpServer(settings().http, registry);
  const url = await listen(server);
  t.after(
    () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  );
  assert.equal((await fetch(url)).status, 401);
  assert.equal((await fetch(new URL("/healthz", url))).status, 200);
  const headers = {
    Authorization: `Bearer ${process.env.TEST_RELAY_TOKEN}`,
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
  };
  assert.equal(
    (
      await fetch(url, {
        method: "POST",
        headers: { ...headers, Origin: "https://evil.example" },
        body: "{}",
      })
    ).status,
    403,
  );
  assert.equal(
    (await fetch(url, { method: "POST", headers, body: "x".repeat(2049) }))
      .status,
    413,
  );
  assert.equal(
    (await fetch(url, { method: "POST", headers, body: "{" })).status,
    400,
  );
  assert.equal((await fetch(url, { headers })).status, 405);
  const chunkedStatus = await new Promise<number | undefined>(
    (resolve, reject) => {
      const request = httpRequest(
        url,
        { method: "POST", headers },
        (response) => {
          response.resume();
          resolve(response.statusCode);
        },
      );
      request.on("error", reject);
      request.write("x".repeat(1024));
      request.end("x".repeat(1025));
    },
  );
  assert.equal(chunkedStatus, 413);
  const client = new Client({ name: "fixture", version: "1" });
  await client.connect(
    new StreamableHTTPClientTransport(url, { requestInit: { headers } }),
  );
  t.after(() => client.close());
  assert.equal((await client.listTools()).tools[0].name, "fixture__echo");
  const answer = await client.callTool({
    name: "fixture__echo",
    arguments: { text: "hello" },
  });
  assert.deepEqual(answer.content, [
    { type: "text", text: '{"text":"hello"}' },
  ]);
  assert.equal(
    (await client.callTool({ name: "fixture__echo", arguments: { text: 5 } }))
      .isError,
    true,
  );
  const concurrent = await Promise.all(
    Array.from({ length: 12 }, (_, index) =>
      client.callTool({
        name: "fixture__echo",
        arguments: { text: String(index) },
      }),
    ),
  );
  assert.equal(concurrent.length, 12);
});

test("stdio upstreams relay tools with JSON Schema 2020-12", async (t) => {
  const registry = new ToolRegistry();
  const config = configSchema.parse({
    http: settings().http,
    upstreams: [
      {
        name: "local",
        transport: "stdio",
        command: process.execPath,
        args: [fileURLToPath(new URL("./fixtures/stdio.mjs", import.meta.url))],
      },
    ],
  });
  const upstream = new Upstream(config.upstreams[0], registry);
  t.after(() => upstream.close());
  await upstream.refresh();
  assert.deepEqual(
    (await registry.call("local__echo", { text: "hello" })).content,
    [{ type: "text", text: "hello" }],
  );
  assert.equal((await registry.call("local__echo", { text: 1 })).isError, true);
});

test("legacy SSE upstreams receive authentication on GET and POST", async (t) => {
  let transport: SSEServerTransport | undefined;
  let postCount = 0;
  const server = createServer(async (request, response) => {
    if (request.headers.authorization !== "Bearer sse-secret") {
      response.writeHead(401).end();
      return;
    }
    if (request.method === "GET") {
      transport = new SSEServerTransport("/messages", response);
      const mcp = new Server(
        { name: "sse-fixture", version: "1" },
        { capabilities: { tools: {} } },
      );
      mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
        tools: [{ name: "ping", inputSchema: { type: "object" } }],
      }));
      mcp.setRequestHandler(CallToolRequestSchema, async () => result("pong"));
      await mcp.connect(transport);
    } else {
      postCount++;
      await transport!.handlePostMessage(request, response);
    }
  });
  const url = await listen(server);
  t.after(
    () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  );
  process.env.SSE_TEST_AUTH = "Bearer sse-secret";
  t.after(() => {
    delete process.env.SSE_TEST_AUTH;
  });
  const registry = new ToolRegistry();
  const config = configSchema.parse({
    http: settings().http,
    upstreams: [
      {
        name: "legacy",
        transport: "sse",
        url: url.href,
        headers: { Authorization: { env: "SSE_TEST_AUTH" } },
      },
    ],
  });
  const upstream = new Upstream(config.upstreams[0], registry);
  t.after(() => upstream.close());
  await upstream.refresh();
  assert.deepEqual((await registry.call("legacy__ping", {})).content, [
    { type: "text", text: '"pong"' },
  ]);
  assert.ok(postCount >= 4);
});

test("upstream discovery follows pagination, preserves structured output and does not replay failures", async (t) => {
  let calls = 0;
  let authorized = 0;
  const server = createServer(async (req, res) => {
    if (req.headers.authorization !== "Bearer upstream-secret") {
      res.writeHead(401).end();
      return;
    }
    authorized++;
    const mcp = new Server(
      { name: "fake-upstream", version: "1" },
      { capabilities: { tools: {} } },
    );
    mcp.setRequestHandler(ListToolsRequestSchema, async (req) =>
      req.params?.cursor === "second"
        ? {
            tools: [
              {
                name: "echo",
                inputSchema: {
                  type: "object",
                  properties: { value: { type: "string" } },
                  required: ["value"],
                },
              },
            ],
          }
        : {
            tools: [{ name: "hidden", inputSchema: { type: "object" } }],
            nextCursor: "second",
          },
    );
    mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
      calls++;
      if (req.params.arguments?.value === "fail")
        throw new Error("secret-upstream-detail");
      return {
        ...result(req.params.arguments),
        structuredContent: req.params.arguments,
      };
    });
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    });
    res.once("close", () => {
      void mcp.close();
    });
    await mcp.connect(transport);
    await transport.handleRequest(req, res);
  });
  const url = await listen(server);
  t.after(
    () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  );
  process.env.UPSTREAM_AUTH = "Bearer upstream-secret";
  t.after(() => {
    delete process.env.UPSTREAM_AUTH;
  });
  const registry = new ToolRegistry();
  const config = configSchema.parse({
    http: settings().http,
    upstreams: [
      {
        name: "remote",
        transport: "http",
        url: url.href,
        headers: { Authorization: { env: "UPSTREAM_AUTH" } },
        allowTools: ["echo"],
      },
    ],
  });
  const upstream = new Upstream(config.upstreams[0], registry);
  t.after(() => upstream.close());
  await Promise.all([upstream.refresh(), upstream.refresh()]);
  assert.deepEqual(
    registry.list().map((tool) => tool.name),
    ["remote__echo"],
  );
  assert.deepEqual(
    (await registry.call("remote__echo", { value: "hello" })).structuredContent,
    { value: "hello" },
  );
  const failed = await registry.call("remote__echo", { value: "fail" });
  assert.equal(failed.isError, true);
  assert.doesNotMatch(JSON.stringify(failed), /secret-upstream-detail/);
  assert.equal(calls, 2);
  assert.ok(authorized >= 5);
  await upstream.refresh();
  assert.equal(registry.list().length, 1);
});

test("fixed commands validate JSON input, isolate secrets and enforce time/output bounds", async () => {
  process.env.DO_NOT_INHERIT_THIS_SECRET = "sensitive";
  const config = configSchema.parse({
    http: settings().http,
    commands: [
      {
        name: "echo",
        description: "test",
        command: process.execPath,
        args: [
          "-e",
          "let s='';process.stdin.on('data',b=>s+=b);process.stdin.on('end',()=>console.log(JSON.stringify({input:JSON.parse(s),leaked:!!process.env.DO_NOT_INHERIT_THIS_SECRET})))",
        ],
        stdinJson: true,
        inputSchema: {
          type: "object",
          properties: { text: { type: "string" } },
          required: ["text"],
          additionalProperties: false,
        },
      },
      {
        name: "hang",
        description: "test",
        command: process.execPath,
        args: ["-e", "setInterval(()=>{},1000)"],
        timeoutMs: 100,
      },
      {
        name: "large",
        description: "test",
        command: process.execPath,
        args: ["-e", "console.log('x'.repeat(2048))"],
        maxOutputBytes: 1024,
      },
    ],
  });
  const registry = new ToolRegistry();
  registerCommands(registry, config.commands);
  const answer = await registry.call("command__echo", {
    text: "$(touch /not-executed)",
  });
  assert.deepEqual(answer.content, [
    {
      type: "text",
      text: JSON.stringify({
        input: { text: "$(touch /not-executed)" },
        leaked: false,
      }),
    },
  ]);
  assert.equal(
    (await registry.call("command__echo", { text: 3 })).isError,
    true,
  );
  assert.match(
    JSON.stringify(await registry.call("command__hang", {})),
    /timed out/,
  );
  assert.match(
    JSON.stringify(await registry.call("command__large", {})),
    /exceeds/,
  );
  delete process.env.DO_NOT_INHERIT_THIS_SECRET;
});
