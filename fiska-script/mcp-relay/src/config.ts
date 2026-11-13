import { readFileSync } from "node:fs";
import { z } from "zod";

const secret = z.union([
  z.object({ env: z.string().min(1) }).strict(),
  z.object({ file: z.string().min(1) }).strict(),
]);
const name = z.string().regex(/^[a-z][a-z0-9_]{0,39}$/);
const env = z.record(z.string(), secret).default({});
const upstream = z
  .object({
    name,
    transport: z.enum(["http", "sse", "stdio"]),
    url: z.url().optional(),
    command: z.string().optional(),
    args: z.array(z.string()).default([]),
    cwd: z.string().optional(),
    env,
    headers: z.record(z.string(), secret).default({}),
    allowTools: z.array(z.string()).optional(),
    timeoutMs: z.number().int().min(1000).max(600000).default(60000),
  })
  .strict()
  .superRefine((value, ctx) => {
    if (value.transport === "stdio" ? !value.command : !value.url)
      ctx.addIssue({
        code: "custom",
        message: "Upstream needs a command or URL for its transport",
      });
    if (value.url) {
      const url = new URL(value.url);
      if (
        !["http:", "https:"].includes(url.protocol) ||
        url.username ||
        url.password
      )
        ctx.addIssue({
          code: "custom",
          message: "Use HTTP(S) URLs without embedded credentials",
        });
    }
  });

export const configSchema = z
  .object({
    http: z
      .object({
        host: z.string().default("0.0.0.0"),
        port: z.number().int().min(0).max(65535).default(8080),
        token: secret.optional(),
        endpoints: z
          .array(
            z
              .object({
                name,
                path: z
                  .string()
                  .regex(
                    /^\/mcp\/[a-z0-9][a-z0-9._-]*(?:\/[a-z0-9][a-z0-9._-]*)*$/,
                  ),
                token: secret,
                allowTools: z
                  .array(z.string().regex(/^[a-zA-Z0-9_.-]{1,128}$/))
                  .min(1),
              })
              .strict(),
          )
          .default([]),
        allowedOrigins: z.array(z.url()).default([]),
        maxBodyBytes: z
          .number()
          .int()
          .positive()
          .max(16777216)
          .default(2097152),
      })
      .strict(),
    dataDir: z.string().default("/data"),
    upstreams: z.array(upstream).default([]),
    rest: z
      .array(
        z
          .object({
            name,
            description: z.string().min(1),
            baseUrl: z.url().refine((value) => {
              const url = new URL(value);
              return (
                url.protocol === "https:" &&
                !url.username &&
                !url.password &&
                !url.search &&
                !url.hash
              );
            }, "REST API base must use HTTPS without embedded credentials, query or fragment"),
            pathPrefixes: z
              .array(z.string().regex(/^\/[a-zA-Z0-9_/-]*$/))
              .min(1),
            auth: z.discriminatedUnion("type", [
              z.object({ type: z.literal("bearer"), token: secret }).strict(),
              z
                .object({
                  type: z.literal("basic"),
                  username: secret,
                  password: secret,
                })
                .strict(),
              z
                .object({
                  type: z.literal("header"),
                  name: z.string().regex(/^[A-Za-z0-9-]+$/),
                  value: secret,
                })
                .strict(),
            ]),
            allowWrites: z.boolean().default(false),
            writeRules: z
              .array(
                z
                  .object({
                    method: z.enum(["POST", "PUT", "PATCH", "DELETE"]),
                    pathPattern: z
                      .string()
                      .max(500)
                      .refine((value) => {
                        if (!value.startsWith("^") || !value.endsWith("$"))
                          return false;
                        try {
                          new RegExp(value);
                          return true;
                        } catch {
                          return false;
                        }
                      }, "Write path patterns must be valid, anchored regular expressions"),
                  })
                  .strict(),
              )
              .optional(),
          })
          .strict(),
      )
      .default([]),
    jira: z
      .object({
        site: z.url(),
        cloudId: z.uuid().optional(),
        email: secret,
        token: secret,
        allowWrites: z.boolean().default(false),
      })
      .strict()
      .optional(),
    github: z
      .object({
        token: secret,
        allowWrites: z.boolean().default(false),
      })
      .strict()
      .optional(),
    google: z
      .object({
        credentialsFile: z.string(),
        drive: z.boolean().default(true),
        gmail: z
          .object({
            query: z.string().default("in:inbox"),
            pollSeconds: z.number().int().min(10).default(60),
            pagesPerPoll: z.number().int().min(1).max(100).default(5),
          })
          .strict()
          .optional(),
      })
      .strict()
      .optional(),
    smtp: z
      .object({
        host: z.string().default("0.0.0.0"),
        port: z.number().int().min(0).max(65535).default(2525),
        recipients: z.array(z.email()).min(1),
        maxMessageBytes: z
          .number()
          .int()
          .min(1024)
          .max(52428800)
          .default(10485760),
        tls: z
          .object({ keyFile: z.string(), certFile: z.string() })
          .strict()
          .optional(),
      })
      .strict()
      .optional(),
    commands: z
      .array(
        z
          .object({
            name,
            description: z.string().min(1),
            command: z.string().min(1),
            args: z.array(z.string()).default([]),
            cwd: z.string().optional(),
            env,
            inputSchema: z
              .record(z.string(), z.unknown())
              .default({ type: "object", additionalProperties: false }),
            stdinJson: z.boolean().default(false),
            readOnly: z.boolean().default(false),
            timeoutMs: z.number().int().min(100).max(600000).default(60000),
            maxOutputBytes: z
              .number()
              .int()
              .min(1024)
              .max(10485760)
              .default(2097152),
          })
          .strict(),
      )
      .default([]),
  })
  .strict()
  .superRefine((value, ctx) => {
    if (!value.http.token && !value.http.endpoints.length)
      ctx.addIssue({
        code: "custom",
        path: ["http"],
        message: "Configure an aggregate token or at least one scoped endpoint",
      });
    for (const key of ["name", "path"] as const) {
      const values = value.http.endpoints.map((endpoint) => endpoint[key]);
      if (new Set(values).size !== values.length)
        ctx.addIssue({
          code: "custom",
          path: ["http", "endpoints"],
          message: `Endpoint ${key} values must be unique`,
        });
    }
    const names = [...value.upstreams, ...value.rest].map((item) => item.name);
    if (
      new Set(names).size !== names.length ||
      names.some((n) => n.includes("__")) ||
      names.some((n) =>
        [
          "relay",
          "mail",
          "gmail",
          "gdrive",
          "jira",
          "github",
          "command",
        ].includes(n),
      )
    )
      ctx.addIssue({
        code: "custom",
        message:
          "Integration names must be unique, contain no double underscore, and not use built-in namespaces",
      });
    if (
      new Set(value.commands.map((c) => c.name)).size !== value.commands.length
    )
      ctx.addIssue({ code: "custom", message: "Command names must be unique" });
    if (value.jira) {
      const url = new URL(value.jira.site);
      if (
        url.protocol !== "https:" ||
        url.username ||
        url.password ||
        !["", "/"].includes(url.pathname) ||
        url.search ||
        url.hash
      )
        ctx.addIssue({
          code: "custom",
          message: "Jira site must be an HTTPS origin",
        });
    }
  });

export type Config = z.infer<typeof configSchema>;
export type Secret = z.infer<typeof secret>;
export type UpstreamConfig = z.infer<typeof upstream>;

export function readSecret(ref: Secret): string {
  const value =
    "env" in ref ? process.env[ref.env] : readFileSync(ref.file, "utf8");
  if (!value?.trim())
    throw new Error("A required credential is missing or empty");
  return value.trim();
}

export function secretMap(
  refs: Record<string, Secret>,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(refs).map(([key, ref]) => [key, readSecret(ref)]),
  );
}

export function loadConfig(
  path = process.env.RELAY_CONFIG ?? "config.json",
): Config {
  const parsed = configSchema.safeParse(JSON.parse(readFileSync(path, "utf8")));
  if (!parsed.success)
    throw new Error(
      `Invalid relay configuration: ${parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ")}`,
    );
  return parsed.data;
}
