import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import {
  CallToolResultSchema,
  type Tool,
} from "@modelcontextprotocol/sdk/types.js";
import { type UpstreamConfig, secretMap } from "./config.js";
import { SafeError, type ToolRegistry } from "./tools.js";

export function childEnvironment(extra: Record<string, string>) {
  const base: Record<string, string> = {};
  for (const key of ["PATH", "HOME", "TMPDIR", "LANG"])
    if (process.env[key]) base[key] = process.env[key]!;
  return { ...base, ...extra };
}

export class Upstream {
  private client?: Client;
  private connecting?: Promise<Client>;
  private refreshing?: Promise<void>;
  private count = 0;
  private error: string | null = null;
  private closed = false;
  constructor(
    readonly config: UpstreamConfig,
    private readonly registry: ToolRegistry,
  ) {}

  status() {
    return {
      name: this.config.name,
      connected: !!this.client,
      tools: this.count,
      error: this.error,
    };
  }

  private connect(): Promise<Client> {
    if (this.closed)
      return Promise.reject(new SafeError("Upstream is shutting down"));
    if (this.client) return Promise.resolve(this.client);
    if (this.connecting) return this.connecting;
    this.connecting = this.open().finally(() => {
      this.connecting = undefined;
    });
    return this.connecting;
  }

  private async open(): Promise<Client> {
    const cfg = this.config;
    const headers = secretMap(cfg.headers);
    const transport =
      cfg.transport === "stdio"
        ? new StdioClientTransport({
            command: cfg.command!,
            args: cfg.args,
            cwd: cfg.cwd,
            env: childEnvironment(secretMap(cfg.env)),
            stderr: "ignore",
          })
        : cfg.transport === "sse"
          ? new SSEClientTransport(new URL(cfg.url!), {
              requestInit: { headers, redirect: "error" },
            })
          : new StreamableHTTPClientTransport(new URL(cfg.url!), {
              requestInit: { headers, redirect: "error" },
            });
    const client = new Client({ name: "fiska-mcp-relay", version: "0.1.0" });
    client.onclose = () => {
      if (this.client === client) this.client = undefined;
    };
    let deadline: NodeJS.Timeout | undefined;
    try {
      await Promise.race([
        client.connect(transport, { timeout: cfg.timeoutMs }),
        new Promise<never>((_, reject) => {
          deadline = setTimeout(
            () => reject(new SafeError("Upstream initialization timed out")),
            cfg.timeoutMs,
          );
        }),
      ]);
      if (this.closed) {
        await client.close();
        throw new SafeError("Upstream is shutting down");
      }
      this.client = client;
      return client;
    } catch {
      await transport.close().catch(() => undefined);
      this.error =
        "Connection failed; check upstream configuration and credentials";
      throw new SafeError(`Upstream ${cfg.name} is unavailable`);
    } finally {
      clearTimeout(deadline);
    }
  }

  refresh(): Promise<void> {
    if (this.refreshing) return this.refreshing;
    this.refreshing = this.discover()
      .catch(() => {
        this.error =
          "Tool discovery failed; check upstream availability, credentials and schemas";
        throw new SafeError(`Could not refresh upstream ${this.config.name}`);
      })
      .finally(() => {
        this.refreshing = undefined;
      });
    return this.refreshing;
  }

  private async discover() {
    const client = await this.connect();
    const definitions: Tool[] = [];
    let cursor: string | undefined;
    const seen = new Set<string>();
    do {
      const page = await client.listTools(cursor ? { cursor } : {}, {
        timeout: this.config.timeoutMs,
      });
      definitions.push(...page.tools);
      cursor = page.nextCursor;
      if (cursor && seen.has(cursor))
        throw new SafeError("Upstream returned a repeated pagination cursor");
      if (cursor) seen.add(cursor);
      if (definitions.length > 10000 || seen.size > 1000)
        throw new SafeError("Upstream tool list is too large");
    } while (cursor);
    const filtered = definitions.filter(
      (tool) =>
        !this.config.allowTools || this.config.allowTools.includes(tool.name),
    );
    this.registry.replaceNamespace(
      `${this.config.name}__`,
      filtered.map((tool) => ({
        definition: { ...tool, name: `${this.config.name}__${tool.name}` },
        call: async (args, signal) => {
          const current = await this.connect();
          // A failed call can already have changed the upstream. Never replay it.
          return CallToolResultSchema.parse(
            await current.callTool(
              { name: tool.name, arguments: args },
              CallToolResultSchema,
              { timeout: this.config.timeoutMs, signal },
            ),
          );
        },
      })),
    );
    this.count = filtered.length;
    this.error = null;
  }

  async close() {
    this.closed = true;
    await this.connecting?.catch(() => undefined);
    await this.client?.close();
  }
}
