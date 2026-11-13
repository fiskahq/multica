import { z } from "zod";
import { loadConfig } from "./config.js";
import { ToolRegistry } from "./tools.js";
import { createHttpServer } from "./server.js";
import { Upstream } from "./upstreams.js";
import { registerServices } from "./services.js";
import { registerCommands } from "./commands.js";
import { MailStore, createSmtp, registerMail } from "./mail.js";
import { GoogleApi, GmailSync, registerGoogle } from "./google.js";

async function main() {
  const config = loadConfig();
  const registry = new ToolRegistry();
  const store = new MailStore(config.dataDir);
  registerMail(registry, store);
  registerServices(registry, config);
  registerCommands(registry, config.commands);
  let gmail: GmailSync | undefined;
  if (config.google) {
    const api = new GoogleApi(config.google.credentialsFile);
    if (config.google.gmail)
      gmail = new GmailSync(config.google.gmail, store, (url) => api.json(url));
    registerGoogle(registry, api, config.google, gmail);
  }
  const upstreams = config.upstreams.map(
    (settings) => new Upstream(settings, registry),
  );
  const status = () => ({
    upstreams: upstreams.map((upstream) => upstream.status()),
    gmail: gmail?.status() ?? { configured: false },
    tools: registry.list().length,
  });
  registry.local(
    "relay__status",
    "Show configured upstream connection status and Gmail import health. Does not return credentials.",
    z.object({}).strict(),
    true,
    async () => status(),
  );
  const refresh = async () => {
    await Promise.allSettled(upstreams.map((upstream) => upstream.refresh()));
    return status();
  };
  registry.local(
    "relay__refresh",
    "Reconnect unavailable upstreams and refresh their tool catalogs. Refresh your client's tool list afterward. Does not retry previous tool calls.",
    z.object({}).strict(),
    true,
    refresh,
  );
  const http = createHttpServer(config.http, registry);
  await new Promise<void>((resolve, reject) => {
    http.once("error", reject);
    http.listen(config.http.port, config.http.host, resolve);
  });
  const smtp = config.smtp ? createSmtp(config.smtp, store) : undefined;
  if (smtp && config.smtp)
    await new Promise<void>((resolve, reject) => {
      smtp.once("error", reject);
      smtp.listen(config.smtp!.port, config.smtp!.host, resolve);
    });
  process.stderr.write(
    JSON.stringify({
      event: "relay_started",
      port: config.http.port,
      smtp: !!smtp,
    }) + "\n",
  );
  gmail?.start();
  void refresh();
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    const timer = setTimeout(() => process.exit(1), 35000).unref();
    await Promise.all([
      new Promise<void>((resolve) => http.close(() => resolve())),
      smtp
        ? new Promise<void>((resolve) => smtp.close(resolve))
        : Promise.resolve(),
      gmail?.stop(),
    ]);
    await Promise.allSettled(upstreams.map((upstream) => upstream.close()));
    store.close();
    clearTimeout(timer);
  };
  process.on("SIGTERM", () => {
    void stop();
  });
  process.on("SIGINT", () => {
    void stop();
  });
}

main().catch(() => {
  process.stderr.write(
    '{"event":"startup_failed","message":"Check configuration, secret files, data directory permissions and port availability"}\n',
  );
  process.exit(1);
});
