import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { createHash, timingSafeEqual } from "node:crypto";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { type Config, type Secret, readSecret } from "./config.js";
import { failure, type ToolRegistry } from "./tools.js";

const digest = (value: string) => createHash("sha256").update(value).digest();

function send(response: ServerResponse, status: number, value: unknown) {
  response
    .writeHead(status, {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    })
    .end(JSON.stringify(value));
}

async function body(request: IncomingMessage, maximum: number) {
  if (Number(request.headers["content-length"] ?? 0) > maximum)
    throw new RangeError();
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    size += chunk.length;
    if (size > maximum) throw new RangeError();
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

export function createHttpServer(
  config: Config["http"],
  registry: ToolRegistry,
) {
  const routes = new Map<
    string,
    { name: string; token: Secret; allowed?: Set<string> }
  >();
  if (config.token)
    routes.set("/mcp", { name: "fiska-mcp-relay", token: config.token });
  for (const endpoint of config.endpoints) {
    routes.set(endpoint.path, {
      name: `fiska-mcp-relay-${endpoint.name}`,
      token: endpoint.token,
      allowed: new Set(endpoint.allowTools),
    });
  }
  const tokens = new Set<string>();
  for (const route of routes.values()) {
    const token = readSecret(route.token);
    if (token.length < 32)
      throw new Error("Relay tokens must contain at least 32 characters");
    if (tokens.has(token))
      throw new Error("Each MCP endpoint must use a different token");
    tokens.add(token);
  }
  const server = createServer(async (request, response) => {
    if (request.url === "/healthz" && request.method === "GET")
      return send(response, 200, { status: "ok" });
    const route = routes.get(request.url ?? "");
    if (!route) return send(response, 404, { error: "Not found" });
    try {
      const token = readSecret(route.token);
      if (token.length < 32) throw new Error("Invalid credential");
      if (
        !timingSafeEqual(
          digest(request.headers.authorization ?? ""),
          digest(`Bearer ${token}`),
        )
      ) {
        response.setHeader("WWW-Authenticate", 'Bearer realm="mcp-relay"');
        return send(response, 401, { error: "Unauthorized" });
      }
    } catch {
      return send(response, 503, { error: "Credential unavailable" });
    }
    const origin = request.headers.origin;
    if (origin && !config.allowedOrigins.includes(origin))
      return send(response, 403, { error: "Origin not allowed" });
    if (request.method !== "POST") {
      response.setHeader("Allow", "POST");
      return send(response, 405, {
        error: "Stateless MCP endpoint accepts POST",
      });
    }
    if (!request.headers["content-type"]?.startsWith("application/json"))
      return send(response, 415, { error: "Use application/json" });
    let data;
    try {
      data = await body(request, config.maxBodyBytes);
    } catch (error) {
      response.setHeader("Connection", "close");
      request.resume();
      return send(response, error instanceof RangeError ? 413 : 400, {
        error: "Invalid or oversized JSON body",
      });
    }
    const mcp = new Server(
      { name: route.name, version: "0.1.0" },
      { capabilities: { tools: {} } },
    );
    mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: registry
        .list()
        .filter((tool) => !route.allowed || route.allowed.has(tool.name)),
    }));
    mcp.setRequestHandler(CallToolRequestSchema, async (request, extra) =>
      route.allowed && !route.allowed.has(request.params.name)
        ? failure("Unknown tool")
        : registry.call(
            request.params.name,
            request.params.arguments ?? {},
            extra.signal,
          ),
    );
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    });
    response.once("close", () => {
      void mcp.close();
    });
    try {
      await mcp.connect(transport);
      await transport.handleRequest(request, response, data);
    } catch {
      if (!response.headersSent)
        send(response, 500, { error: "MCP request failed" });
      await mcp.close();
    }
  });
  server.requestTimeout = 30000;
  server.headersTimeout = 10000;
  return server;
}
