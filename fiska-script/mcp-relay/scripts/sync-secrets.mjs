import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const manifest = resolve(
  process.argv[2] ?? join(root, ".secrets/bindings.json"),
);
const directory = dirname(manifest);
let stage;
let locked = false;
const lock = join(directory, ".sync-lock");
try {
  const bindings = JSON.parse(readFileSync(manifest, "utf8"));
  if (!bindings || typeof bindings !== "object" || Array.isArray(bindings))
    throw new Error();
  for (const [name, reference] of Object.entries(bindings)) {
    if (
      !/^[a-z][a-z0-9-]{0,79}$/.test(name) ||
      name === "relay-token" ||
      typeof reference !== "string" ||
      !/^op:\/\/[^/]+\/[^/]+\/.+/.test(reference) ||
      /[\r\n\u0000]/.test(reference)
    )
      throw new Error();
  }
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  try {
    mkdirSync(lock, { mode: 0o700 });
  } catch {
    throw new Error(
      "Secret sync is locked. Another sync may be running; see the README for recovery.",
    );
  }
  locked = true;
  stage = mkdtempSync(join(directory, ".sync-"));
  const entries = Object.entries(bindings);
  if (entries.length) {
    const envFile = join(stage, "references.env");
    writeFileSync(
      envFile,
      entries
        .map(
          ([, reference], index) =>
            `RELAY_SYNC_SECRET_${index}=${JSON.stringify(reference)}`,
        )
        .join("\n") + "\n",
      { mode: 0o600 },
    );
    const writer = `
      const { writeFileSync } = require("node:fs");
      const { join } = require("node:path");
      const names = ${JSON.stringify(entries.map(([name]) => name))};
      for (const [index, name] of names.entries()) {
        const value = process.env["RELAY_SYNC_SECRET_" + index];
        if (!value?.trim() || value.startsWith("op://")) process.exit(1);
        writeFileSync(join(${JSON.stringify(stage)}, name), value, { mode: 0o600, flag: "wx" });
      }
    `;
    try {
      execFileSync(
        "op",
        [
          "run",
          "--env-file",
          envFile,
          "--",
          process.execPath,
          "--input-type=commonjs",
          "-e",
          writer,
        ],
        {
          stdio: ["ignore", "pipe", "pipe"],
          timeout: 180000,
          maxBuffer: 1048576,
        },
      );
    } catch {
      throw new Error(
        "Could not resolve credentials from 1Password. Check the desktop login and access. Existing files were preserved.",
      );
    }
  }
  for (const name of Object.keys(bindings))
    renameSync(join(stage, name), join(directory, name));
  console.log(
    `Updated ${Object.keys(bindings).length} credential files. Values were not printed.`,
  );
} catch (error) {
  console.error(
    error instanceof Error &&
      /^(Could not resolve|Secret sync is locked)/.test(error.message)
      ? error.message
      : "Secret sync failed. Check the manifest format, permissions and 1Password login.",
  );
  process.exitCode = 1;
} finally {
  if (stage) rmSync(stage, { recursive: true, force: true });
  if (locked) rmSync(lock, { recursive: true, force: true });
}
