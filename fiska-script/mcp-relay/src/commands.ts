import { spawn } from "node:child_process";
import { type Config, secretMap } from "./config.js";
import { childEnvironment } from "./upstreams.js";
import { result, SafeError, type ToolRegistry } from "./tools.js";
import type { Tool } from "@modelcontextprotocol/sdk/types.js";

type Command = Config["commands"][number];

export function runCommand(
  config: Command,
  input: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new SafeError("Command cancelled"));
    const child = spawn(config.command, config.args, {
      cwd: config.cwd,
      env: childEnvironment(secretMap(config.env)),
      shell: false,
      detached: process.platform !== "win32",
      stdio: ["pipe", "pipe", "pipe"],
    });
    const chunks: Buffer[] = [];
    let size = 0;
    let failure: string | undefined;
    const kill = (message: string) => {
      failure ??= message;
      try {
        if (child.pid && process.platform !== "win32")
          process.kill(-child.pid, "SIGKILL");
        else child.kill("SIGKILL");
      } catch {
        /* Already exited. */
      }
    };
    const timer = setTimeout(
      () => kill("Command timed out; inspect its state before retrying"),
      config.timeoutMs,
    );
    const abort = () =>
      kill("Command cancelled; inspect its state before retrying");
    signal?.addEventListener("abort", abort, { once: true });
    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    };
    child.stdout.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > config.maxOutputBytes)
        kill("Command output exceeds its limit");
      else chunks.push(chunk);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > config.maxOutputBytes)
        kill("Command output exceeds its limit");
    });
    child.stdin.on("error", () => {});
    child.once("error", () => {
      cleanup();
      reject(new SafeError("Could not start configured command"));
    });
    child.once("close", (code) => {
      cleanup();
      if (failure || code !== 0)
        return reject(
          new SafeError(
            failure ??
              `Command exited with code ${code}; inspect its state before retrying`,
          ),
        );
      const text = Buffer.concat(chunks).toString("utf8");
      try {
        resolve(JSON.parse(text));
      } catch {
        resolve({ stdout: text });
      }
    });
    child.stdin.end(config.stdinJson ? JSON.stringify(input) : undefined);
  });
}

export function registerCommands(registry: ToolRegistry, commands: Command[]) {
  for (const command of commands)
    registry.add(
      {
        name: `command__${command.name}`,
        description: command.description,
        inputSchema: command.inputSchema as Tool["inputSchema"],
        annotations: {
          readOnlyHint: command.readOnly,
          destructiveHint: !command.readOnly,
          openWorldHint: true,
        },
      },
      async (args, signal) => result(await runCommand(command, args, signal)),
    );
}
