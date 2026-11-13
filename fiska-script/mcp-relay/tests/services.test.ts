import { test } from "node:test";
import assert from "node:assert/strict";
import { configSchema } from "../src/config.js";
import { apiUrl, boundedBody } from "../src/http-client.js";
import { registerServices } from "../src/services.js";
import { GoogleApi, registerGoogle } from "../src/google.js";
import { ToolRegistry } from "../src/tools.js";

test("API paths cannot override the host or Jira cloud prefix", () => {
  for (const path of [
    "//evil.example",
    "/../outside",
    "/%2e%2e/outside",
    "/foo\\bar",
    "/zones/..\n/user/tokens",
    "/test?q=1",
    "https://evil.example",
  ])
    assert.throws(() => apiUrl("https://api.atlassian.com/ex/jira/id", path));
  assert.equal(
    apiUrl("https://api.atlassian.com/ex/jira/id", "/rest/api/3/myself").href,
    "https://api.atlassian.com/ex/jira/id/rest/api/3/myself",
  );
});

test("configured REST tools constrain hosts, paths, writes and credential forwarding", async (t) => {
  process.env.REST_TOKEN = "fake-token";
  process.env.REST_USER = "operator@example.com";
  const calls: { url: string; init: RequestInit }[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (url, init) => {
    calls.push({ url: String(url), init: init! });
    return new Response('{"results":[]}', {
      headers: { "content-type": "application/json", link: "next-page" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = original;
    delete process.env.REST_TOKEN;
    delete process.env.REST_USER;
  });
  const config = configSchema.parse({
    http: { token: { env: "REST_TOKEN" } },
    rest: [
      {
        name: "cloudflare",
        description: "test",
        baseUrl: "https://api.cloudflare.com/client/v4",
        pathPrefixes: ["/zones/", "/zones"],
        auth: { type: "bearer", token: { env: "REST_TOKEN" } },
      },
      {
        name: "confluence",
        description: "test",
        baseUrl: "https://example.atlassian.net",
        pathPrefixes: ["/wiki/api/v2/"],
        auth: {
          type: "basic",
          username: { env: "REST_USER" },
          password: { env: "REST_TOKEN" },
        },
      },
    ],
  });
  const registry = new ToolRegistry();
  registerServices(registry, config);
  const result = await registry.call("cloudflare__request", {
    path: "/zones",
    query: { name: "example.com" },
  });
  assert.match(JSON.stringify(result), /next-page/);
  assert.equal(
    calls[0].url,
    "https://api.cloudflare.com/client/v4/zones?name=example.com",
  );
  assert.equal(
    new Headers(calls[0].init.headers).get("authorization"),
    "Bearer fake-token",
  );
  assert.equal(
    (
      await registry.call("cloudflare__request", {
        path: "/zones/../user/tokens",
      })
    ).isError,
    true,
  );
  assert.equal(
    (await registry.call("cloudflare__request", { path: "/user/tokens" }))
      .isError,
    true,
  );
  assert.equal(
    (await registry.call("cloudflare__request", { path: "/zones-other" }))
      .isError,
    true,
  );
  assert.equal(
    (
      await registry.call("cloudflare__request", {
        method: "POST",
        path: "/zones",
      })
    ).isError,
    true,
  );
  assert.equal(calls.length, 1);
  await registry.call("confluence__request", { path: "/wiki/api/v2/spaces" });
  assert.equal(
    new Headers(calls[1].init.headers).get("authorization"),
    `Basic ${Buffer.from("operator@example.com:fake-token").toString("base64")}`,
  );
});

test("Jira and GitHub use server credentials, enforce write settings and preserve pagination", async (t) => {
  process.env.SERVICE_TOKEN = "fake-token";
  process.env.JIRA_TEST_EMAIL = "operator@example.com";
  const original = globalThis.fetch;
  const calls: { url: string; init: RequestInit }[] = [];
  globalThis.fetch = (async (url, init) => {
    calls.push({ url: String(url), init: init! });
    return new Response('{"nextPageToken":"second","items":[]}', {
      headers: {
        "Content-Type": "application/json",
        Link: '<https://api.github.com/example?page=2>; rel="next"',
      },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = original;
    delete process.env.SERVICE_TOKEN;
    delete process.env.JIRA_TEST_EMAIL;
  });
  const config = configSchema.parse({
    http: { token: { env: "SERVICE_TOKEN" } },
    jira: {
      site: "https://example.atlassian.net",
      cloudId: "12345678-1234-4234-9234-123456789abc",
      email: { env: "JIRA_TEST_EMAIL" },
      token: { env: "SERVICE_TOKEN" },
    },
    github: { token: { env: "SERVICE_TOKEN" } },
  });
  const registry = new ToolRegistry();
  registerServices(registry, config);
  const answer = await registry.call("jira__request", {
    path: "/rest/api/3/search/jql",
    query: { jql: "project = MS" },
  });
  assert.match(JSON.stringify(answer), /nextPageToken/);
  assert.match(
    calls[0].url,
    /^https:\/\/api.atlassian.com\/ex\/jira\/12345678/,
  );
  assert.equal(
    new Headers(calls[0].init.headers).get("authorization"),
    `Basic ${Buffer.from("operator@example.com:fake-token").toString("base64")}`,
  );
  assert.equal(calls[0].init.redirect, "error");
  assert.equal(
    (
      await registry.call("github__request", {
        method: "POST",
        path: "/repos/a/b/issues",
        body: {},
      })
    ).isError,
    true,
  );
  assert.equal(calls.length, 1);
  assert.match(
    JSON.stringify(
      await registry.call("github__request", { path: "/repos/a/b/pulls" }),
    ),
    /rel=/,
  );
  assert.equal(
    new Headers(calls[1].init.headers).get("authorization"),
    "Bearer fake-token",
  );
});

test("Drive tools encode query parameters and export text or binary", async () => {
  class FakeGoogle extends GoogleApi {
    urls: URL[] = [];
    override async json(url: URL) {
      this.urls.push(url);
      return { files: [], nextPageToken: "next" };
    }
    override async bytes(url: URL) {
      this.urls.push(url);
      return { bytes: Buffer.from("document"), contentType: "text/plain" };
    }
  }
  const api = new FakeGoogle("unused");
  const registry = new ToolRegistry();
  registerGoogle(registry, api, { credentialsFile: "unused", drive: true });
  await registry.call("gdrive__list", {
    q: "name contains 'budget'",
    driveId: "shared-drive",
  });
  assert.equal(api.urls[0].searchParams.get("q"), "name contains 'budget'");
  assert.equal(api.urls[0].searchParams.get("corpora"), "drive");
  assert.match(
    JSON.stringify(
      await registry.call("gdrive__export", { fileId: "document-id" }),
    ),
    /document/,
  );
  assert.equal(
    (await registry.call("gdrive__get", { fileId: "../secret" })).isError,
    true,
  );
  assert.equal(api.urls.length, 2);
});

test("streamed response bounds are enforced", async () => {
  await assert.rejects(boundedBody(new Response("12345"), 4), /exceeds/);
});
