import { test, type TestContext } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createConnection } from "node:net";
import { once } from "node:events";
import { MailStore, createSmtp } from "../src/mail.js";
import { GmailSync } from "../src/google.js";
import { HttpError } from "../src/http-client.js";
import { configSchema } from "../src/config.js";

const raw = Buffer.from(
  'From: sender@example.com\r\nTo: factory@relay.local\r\nSubject: Mail fixture\r\nMessage-ID: <fixture@example.com>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="test"\r\n\r\n--test\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nReceived body\r\n--test\r\nContent-Type: text/plain\r\nContent-Disposition: attachment; filename="hello.txt"\r\nContent-Transfer-Encoding: base64\r\n\r\naGVsbG8=\r\n--test--\r\n',
);

function storeFor(t: TestContext) {
  const directory = mkdtempSync(join(tmpdir(), "relay-mail-"));
  const store = new MailStore(directory);
  t.after(() => {
    store.close();
    rmSync(directory, { recursive: true, force: true });
  });
  return { store, directory };
}

test("mail persists with atomic duplicate suppression and bounded attachment reads", async (t) => {
  const { store, directory } = storeFor(t);
  const second = new MailStore(directory);
  t.after(() => second.close());
  const ids = await Promise.all([
    store.ingest(raw, "smtp", "same"),
    second.ingest(raw, "smtp", "same"),
  ]);
  assert.equal(ids[0], ids[1]);
  assert.equal(store.list("", 25).messages.length, 1);
  assert.match(second.get(ids[0]).text, /Received body/);
  const chunk = store.attachment(ids[0], "0", 0, 2);
  assert.equal(Buffer.from(chunk.base64, "base64").toString(), "he");
  assert.equal(chunk.nextOffset, 2);
  assert.equal(store.attachment(ids[0], "0", 2, 10).nextOffset, null);
  assert.equal(store.list("%", 25).messages.length, 0);
  await store.ingest(raw, "gmail", "second");
  const page = store.list("", 1);
  assert.equal(store.list("", 1, page.nextBefore).messages[0].id, ids[0]);
});

test("Gmail resumes pages, skips duplicates, and does not advance past a failed import", async (t) => {
  const { store } = storeFor(t);
  let fail = true;
  const seen: string[] = [];
  const sync = new GmailSync(
    { query: "in:inbox", pollSeconds: 60, pagesPerPoll: 1 },
    store,
    async (url) => {
      seen.push(url.toString());
      if (url.pathname.endsWith("/profile"))
        return { emailAddress: "operator@example.com" };
      if (url.pathname.endsWith("/messages"))
        return url.searchParams.get("pageToken") === "page2"
          ? { messages: [{ id: "2" }] }
          : { messages: [{ id: "1" }, { id: "gone" }], nextPageToken: "page2" };
      if (url.pathname.endsWith("/gone")) throw new HttpError(404);
      if (url.pathname.endsWith("/2") && fail)
        throw new Error("secret must not leak");
      return { raw: raw.toString("base64url") };
    },
  );
  assert.deepEqual(await sync.sync(), { imported: 1, hasMore: true });
  await assert.rejects(sync.sync());
  assert.doesNotMatch(sync.status().lastError!, /secret must/);
  fail = false;
  assert.deepEqual(await sync.sync(), { imported: 1, hasMore: false });
  assert.equal(store.list("", 25).messages.length, 2);
  assert.deepEqual(await sync.sync(), { imported: 0, hasMore: true });
  assert.equal(seen.filter((url) => url.includes("/messages/1?")).length, 1);
  const key = JSON.stringify(["operator@example.com", "in:inbox"]);
  store.advance(key, "stale-cursor", "must-not-win");
  assert.equal(store.cursor(key), "page2");
});

test("SMTP accepts only configured recipients and acknowledges persisted mail", async (t) => {
  const { store } = storeFor(t);
  const config = configSchema.parse({
    http: { token: { env: "TEST_TOKEN" } },
    smtp: {
      port: 0,
      host: "127.0.0.1",
      recipients: ["factory@relay.local"],
      maxMessageBytes: 1024,
    },
  });
  const smtp = createSmtp(config.smtp!, store);
  await new Promise<void>((resolve) => smtp.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise<void>((resolve) => smtp.close(resolve)));
  const address = (
    smtp as unknown as { server: { address(): { port: number } } }
  ).server.address();
  const socket = createConnection({ host: "127.0.0.1", port: address.port });
  t.after(() => socket.destroy());
  let pending = "";
  const responses: string[] = [];
  let wake: (() => void) | undefined;
  socket.on("data", (bytes) => {
    pending += bytes.toString();
    let end;
    while ((end = pending.indexOf("\r\n")) >= 0) {
      const line = pending.slice(0, end);
      pending = pending.slice(end + 2);
      if (/^\d{3} /.test(line)) {
        responses.push(line);
        wake?.();
      }
    }
  });
  const read = async () => {
    while (!responses.length)
      await new Promise<void>((resolve) => {
        wake = resolve;
      });
    return responses.shift()!;
  };
  const command = async (value: string | Buffer) => {
    socket.write(value);
    return read();
  };
  await once(socket, "connect");
  assert.match(await read(), /^220/);
  assert.match(await command("EHLO local\r\n"), /^250/);
  assert.match(await command("MAIL FROM:<sender@example.com>\r\n"), /^250/);
  assert.match(await command("RCPT TO:<outside@example.com>\r\n"), /^550/);
  assert.match(await command("RCPT TO:<factory@relay.local>\r\n"), /^250/);
  assert.match(await command("DATA\r\n"), /^354/);
  assert.match(
    await command(Buffer.concat([raw, Buffer.from("\r\n.\r\n")])),
    /^250/,
  );
  assert.equal(store.list("Mail fixture", 10).messages.length, 1);
  await command("MAIL FROM:<sender@example.com>\r\n");
  await command("RCPT TO:<factory@relay.local>\r\n");
  await command("DATA\r\n");
  assert.match(await command("x".repeat(2048) + "\r\n.\r\n"), /^552/);
  assert.equal(store.list("", 10).messages.length, 1);
  assert.match(await command("QUIT\r\n"), /^221/);
});
