import { randomBytes } from "node:crypto";
import {
  copyFileSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  constants,
} from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const directory = fileURLToPath(new URL("../", import.meta.url));
for (const name of [".secrets", "data", "data/mermig-home"])
  mkdirSync(join(directory, name), { recursive: true, mode: 0o700 });
function create(path, data) {
  try {
    writeFileSync(join(directory, path), data, { flag: "wx", mode: 0o600 });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
  }
}
create(".secrets/relay-token", randomBytes(32).toString("hex") + "\n");
create(
  ".env",
  `RELAY_UID=${process.getuid?.() ?? 1000}\nRELAY_GID=${process.getgid?.() ?? 1000}\n`,
);
try {
  copyFileSync(
    join(directory, "config.example.json"),
    join(directory, "config.json"),
    constants.COPYFILE_EXCL,
  );
} catch (error) {
  if (error.code !== "EEXIST") throw error;
}
const config = JSON.parse(readFileSync(join(directory, "config.json"), "utf8"));
for (const endpoint of config.http.endpoints ?? []) {
  const match = /^\/run\/relay-secrets\/([a-z][a-z0-9-]{0,79})$/.exec(
    endpoint.token.file ?? "",
  );
  if (match)
    create(`.secrets/${match[1]}`, randomBytes(32).toString("hex") + "\n");
}
console.log(
  "Prepared config.json, .env, relay and endpoint tokens, and data/. Existing files were preserved.",
);
