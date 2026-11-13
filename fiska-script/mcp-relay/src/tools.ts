import { Ajv } from "ajv";
import { Ajv2020 } from "ajv/dist/2020.js";
import { z } from "zod";
import type { CallToolResult, Tool } from "@modelcontextprotocol/sdk/types.js";

export class SafeError extends Error {}
export const result = (value: unknown): CallToolResult => ({
  content: [{ type: "text", text: JSON.stringify(value) }],
});
export const failure = (message: string): CallToolResult => ({
  isError: true,
  content: [{ type: "text", text: message }],
});

export class ToolRegistry {
  private readonly tools = new Map<
    string,
    {
      definition: Tool;
      call: (
        args: Record<string, unknown>,
        signal?: AbortSignal,
      ) => Promise<CallToolResult>;
    }
  >();
  private readonly ajv = new Ajv({ strict: false, validateFormats: false });
  private readonly ajv2020 = new Ajv2020({
    strict: false,
    validateFormats: false,
  });

  add(
    definition: Tool,
    call: (
      args: Record<string, unknown>,
      signal?: AbortSignal,
    ) => Promise<CallToolResult>,
  ) {
    if (
      !/^[a-zA-Z0-9_.-]{1,128}$/.test(definition.name) ||
      this.tools.has(definition.name)
    )
      throw new Error(`Invalid or duplicate tool name: ${definition.name}`);
    const validator = String(definition.inputSchema.$schema ?? "").includes(
      "draft-07",
    )
      ? this.ajv
      : this.ajv2020;
    const validate = validator.compile(definition.inputSchema);
    this.tools.set(definition.name, {
      definition,
      call: async (args, signal) => {
        if (!validate(args))
          throw new SafeError("Arguments do not match the tool input schema");
        return call(args, signal);
      },
    });
  }

  local<T extends z.ZodType>(
    name: string,
    description: string,
    schema: T,
    readOnly: boolean,
    call: (args: z.infer<T>) => Promise<unknown>,
  ) {
    this.add(
      {
        name,
        description,
        inputSchema: z.toJSONSchema(schema, {
          target: "draft-7",
          io: "input",
        }) as Tool["inputSchema"],
        annotations: {
          readOnlyHint: readOnly,
          destructiveHint: !readOnly,
          openWorldHint: true,
        },
      },
      async (args) => result(await call(schema.parse(args))),
    );
  }

  list() {
    return [...this.tools.values()].map((tool) => tool.definition);
  }

  replaceNamespace(
    prefix: string,
    entries: {
      definition: Tool;
      call: (
        args: Record<string, unknown>,
        signal?: AbortSignal,
      ) => Promise<CallToolResult>;
    }[],
  ) {
    const replacement = new ToolRegistry();
    for (const entry of entries) {
      if (!entry.definition.name.startsWith(prefix))
        throw new Error("Invalid namespace");
      replacement.add(entry.definition, entry.call);
    }
    for (const key of this.tools.keys())
      if (key.startsWith(prefix)) this.tools.delete(key);
    for (const [key, value] of replacement.tools) this.tools.set(key, value);
  }

  async call(
    name: string,
    args: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<CallToolResult> {
    const tool = this.tools.get(name);
    if (!tool) return failure("Unknown tool");
    const start = Date.now();
    let ok = false;
    try {
      const answer = await tool.call(args, signal);
      ok = !answer.isError;
      return answer;
    } catch (error) {
      return failure(
        error instanceof SafeError
          ? error.message
          : "Service request failed. Check credentials, connectivity and service configuration.",
      );
    } finally {
      process.stderr.write(
        JSON.stringify({
          event: "tool_call",
          tool: name,
          ok,
          durationMs: Date.now() - start,
        }) + "\n",
      );
    }
  }
}
