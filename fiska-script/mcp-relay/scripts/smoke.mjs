import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const root = fileURLToPath(new URL("../", import.meta.url));
const configFile = resolve(
  process.env.RELAY_CONFIG_FILE ?? join(root, "config.json"),
);
const config = JSON.parse(readFileSync(configFile, "utf8"));
const url = new URL(
  process.env.RELAY_URL ??
    `${process.env.RELAY_BASE_URL ?? "http://127.0.0.1:8090"}/mcp`,
);
function readToken(ref) {
  if (ref.env) return process.env[ref.env]?.trim();
  const match = /^\/run\/relay-secrets\/([a-z][a-z0-9-]{0,79})$/.exec(ref.file);
  return readFileSync(
    match ? join(dirname(configFile), ".secrets", match[1]) : ref.file,
    "utf8",
  ).trim();
}
const targets = (config.http.endpoints ?? []).map((endpoint) => ({
  name: endpoint.name,
  url: new URL(endpoint.path, url),
  token: readToken(endpoint.token),
  allowTools: endpoint.allowTools,
}));
if (config.http.token)
  targets.push({
    name: "aggregate",
    url,
    token: process.env.RELAY_TOKEN_FILE
      ? readFileSync(process.env.RELAY_TOKEN_FILE, "utf8").trim()
      : readToken(config.http.token),
  });
assert.ok(targets.length, "No configured MCP endpoints");
assert.equal((await fetch(new URL("/healthz", url))).status, 200);
const services = process.argv.includes("--services");
for (const target of targets) {
  assert.equal(
    (await fetch(target.url)).status,
    401,
    `${target.name}: missing token was accepted`,
  );
  for (const other of targets.filter((entry) => entry !== target)) {
    assert.equal(
      (
        await fetch(target.url, {
          headers: { Authorization: `Bearer ${other.token}` },
        })
      ).status,
      401,
      `${target.name}: another endpoint's token was accepted`,
    );
  }
  const client = new Client({ name: "relay-smoke", version: "0.1.0" });
  try {
    await client.connect(
      new StreamableHTTPClientTransport(target.url, {
        requestInit: { headers: { Authorization: `Bearer ${target.token}` } },
      }),
    );
    const tools = (await client.listTools()).tools;
    const names = tools.map((tool) => tool.name);
    if (target.allowTools) {
      assert.deepEqual(
        names.sort(),
        [...target.allowTools].sort(),
        `${target.name}: unexpected tool catalog`,
      );
      for (const name of ["relay__status", "mail__list"].filter(
        (name) => !target.allowTools.includes(name),
      )) {
        assert.equal(
          (await client.callTool({ name, arguments: {} })).isError,
          true,
          `${target.name}: a tool outside the allowlist ran`,
        );
      }
    } else {
      for (const name of ["relay__status", "mail__list"]) {
        assert.ok(
          !(await client.callTool({ name, arguments: {} })).isError,
          `${name} returned an error`,
        );
      }
    }
    let probes = 0;
    if (services && target.allowTools) {
      for (const name of names) {
        const args =
          name === "jira__request"
            ? { path: "/rest/api/3/myself" }
            : name === "jira_triage__request"
              ? {
                  path: "/rest/api/3/issue/createmeta/MS/issuetypes",
                  query: { maxResults: 1 },
                }
              : name === "github__request"
                ? { path: "/user" }
                : name === "confluence__request"
                  ? { path: "/wiki/api/v2/spaces", query: { limit: 1 } }
                  : /^cloudflare_[a-z_]+__request$/.test(name)
                    ? { path: "/zones", query: { per_page: 1 } }
                    : undefined;
        if (!args) continue;
        const answer = await client.callTool({ name, arguments: args });
        assert.ok(!answer.isError, `${target.name}: provider read failed`);
        if (name.startsWith("cloudflare_")) {
          const response = JSON.parse(
            answer.content.find((item) => item.type === "text").text,
          );
          assert.equal(
            response.data.success,
            true,
            `${target.name}: Cloudflare rejected the request`,
          );
        }
        probes++;
      }
    }
    console.log(
      `${target.name}: authentication, ${names.length} tools, isolation checks passed${probes ? `; ${probes} provider read passed` : ""}.`,
    );
  } finally {
    await client.close();
  }
}
console.log(
  `MCP smoke passed for ${targets.length} endpoints. Response contents and credentials were not printed.`,
);
