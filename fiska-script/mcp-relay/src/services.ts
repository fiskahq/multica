import { z } from "zod";
import { type Config, readSecret } from "./config.js";
import { apiUrl, request } from "./http-client.js";
import { SafeError, type ToolRegistry } from "./tools.js";

const input = z
  .object({
    method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]).default("GET"),
    path: z.string().min(1).max(2000),
    query: z
      .record(z.string(), z.union([z.string(), z.number(), z.boolean()]))
      .default({}),
    body: z.unknown().optional(),
  })
  .strict();

export function registerServices(registry: ToolRegistry, config: Config) {
  for (const settings of config.rest) {
    registry.local(
      `${settings.name}__request`,
      settings.description,
      input,
      !settings.allowWrites,
      async ({ method, path, query, body }) => {
        const url = apiUrl(settings.baseUrl, path, query);
        if (
          !settings.pathPrefixes.some(
            (prefix) =>
              path === prefix ||
              path.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`),
          )
        )
          throw new SafeError("API path is outside the configured integration");
        if (method !== "GET" && !settings.allowWrites)
          throw new SafeError("Writes are disabled for this integration");
        if (
          method !== "GET" &&
          settings.writeRules !== undefined &&
          !settings.writeRules.some(
            (rule) =>
              rule.method === method && new RegExp(rule.pathPattern).test(path),
          )
        )
          throw new SafeError(
            "Write route is not allowed for this integration",
          );
        const auth = settings.auth;
        const headers: Record<string, string> = {
          Accept: "application/json",
          "Content-Type": "application/json",
        };
        if (auth.type === "bearer")
          headers.Authorization = `Bearer ${readSecret(auth.token)}`;
        else if (auth.type === "basic")
          headers.Authorization = `Basic ${Buffer.from(`${readSecret(auth.username)}:${readSecret(auth.password)}`).toString("base64")}`;
        else headers[auth.name] = readSecret(auth.value);
        const response = await request(url, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
        });
        return { data: response.data, link: response.headers.get("link") };
      },
    );
  }
  if (config.github) {
    const settings = config.github;
    registry.local(
      "github__request",
      "GitHub REST API. Examples: GET /repos/OWNER/REPO/pulls, /issues, /actions/runs; POST /repos/OWNER/REPO/pulls or /issues/NUMBER/comments. Query parameters are separate. Pagination is returned in link. Writes require allowWrites in server configuration.",
      input,
      !settings.allowWrites,
      async ({ method, path, query, body }) => {
        if (method !== "GET" && !settings.allowWrites)
          throw new SafeError("GitHub writes are disabled");
        const { data, headers } = await request(
          apiUrl("https://api.github.com", path, query),
          {
            method,
            headers: {
              Authorization: `Bearer ${readSecret(settings.token)}`,
              Accept: "application/vnd.github+json",
              "Content-Type": "application/json",
              "X-GitHub-Api-Version": "2026-03-10",
            },
            body: body === undefined ? undefined : JSON.stringify(body),
          },
        );
        return { data, link: headers.get("link") };
      },
    );
  }
  if (config.jira) {
    const settings = config.jira;
    const base = settings.cloudId
      ? `https://api.atlassian.com/ex/jira/${settings.cloudId}`
      : settings.site;
    registry.local(
      "jira__request",
      "Jira REST v3. Examples: GET /rest/api/3/search/jql with query.jql; GET /rest/api/3/issue/MS-123; GET /rest/api/3/field; POST /rest/api/3/issue/MS-123/transitions or /comment; PUT /rest/api/3/issue/MS-123. Preserve nextPageToken/startAt pagination fields. Writes require allowWrites.",
      input,
      !settings.allowWrites,
      async ({ method, path, query, body }) => {
        if (!path.startsWith("/rest/api/3/"))
          throw new SafeError("Only Jira REST v3 paths are allowed");
        if (method !== "GET" && !settings.allowWrites)
          throw new SafeError("Jira writes are disabled");
        const authorization = Buffer.from(
          `${readSecret(settings.email)}:${readSecret(settings.token)}`,
        ).toString("base64");
        return (
          await request(apiUrl(base, path, query), {
            method,
            headers: {
              Authorization: `Basic ${authorization}`,
              Accept: "application/json",
              "Content-Type": "application/json",
            },
            body: body === undefined ? undefined : JSON.stringify(body),
          })
        ).data;
      },
    );
  }
}
