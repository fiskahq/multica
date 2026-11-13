import { SafeError } from "./tools.js";

export class HttpError extends SafeError {
  constructor(readonly status: number) {
    super(`Service returned HTTP ${status}`);
  }
}

export async function boundedBody(
  response: Response,
  limit = 8388608,
): Promise<Buffer> {
  if (!response.body) return Buffer.alloc(0);
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit)
        throw new SafeError(
          `Response exceeds the ${limit} byte limit. Request a smaller result.`,
        );
      chunks.push(value);
    }
    return Buffer.concat(chunks);
  } finally {
    await reader.cancel();
  }
}

export async function request(
  url: string | URL,
  init: RequestInit = {},
  limit?: number,
): Promise<{ data: any; headers: Headers }> {
  const response = await fetch(url, {
    ...init,
    redirect: "error",
    signal: init.signal ?? AbortSignal.timeout(30000),
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new HttpError(response.status);
  }
  const body = await boundedBody(response, limit);
  return {
    data: body.length
      ? response.headers.get("content-type")?.includes("json")
        ? JSON.parse(body.toString("utf8"))
        : body.toString("utf8")
      : null,
    headers: response.headers,
  };
}

export function apiUrl(
  base: string,
  path: string,
  query: Record<string, string | number | boolean> = {},
): URL {
  if (
    !path.startsWith("/") ||
    path.startsWith("//") ||
    /[\u0000-\u0020\u007f\\#?%]/.test(path) ||
    path.split("/").some((part) => part === "." || part === "..")
  )
    throw new SafeError(
      "Use an API path starting with /; supply query parameters separately",
    );
  const root = new URL(base);
  const url = new URL(root.origin + root.pathname.replace(/\/$/, "") + path);
  if (url.origin !== root.origin)
    throw new SafeError("API host is not allowed");
  for (const [key, value] of Object.entries(query))
    url.searchParams.set(key, String(value));
  return url;
}
