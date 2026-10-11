// Every call goes to /api/*, which next.config.ts proxies to the backend (same origin, cookie auth).

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** FastAPI errors are `{detail: "text"}` or `{detail: [{loc, msg}, ...]}` for validation. */
export function formatDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const rawLoc = (d as { loc?: unknown }).loc;
          const loc = Array.isArray(rawLoc) ? rawLoc.filter((p) => p !== "body").join(".") : "";
          const msg = String((d as { msg: unknown }).msg).replace(/^Value error, /, "");
          return loc ? `${loc}: ${msg}` : msg;
        }
        return null;
      })
      .filter(Boolean);
    return parts.length ? parts.join("; ") : null;
  }
  return null;
}

export const AUTH_EXPIRED_EVENT = "auth:expired";

interface Options extends Omit<RequestInit, "body"> {
  json?: unknown;
}

export async function api<T>(path: string, { json, ...init }: Options = {}): Promise<T> {
  const headers = new Headers(init.headers);
  let body: string | undefined;
  if (json !== undefined) {
    headers.set("content-type", "application/json");
    body = JSON.stringify(json);
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers,
      body,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    }
    const detail = data && typeof data === "object" ? (data as { detail?: unknown }).detail : null;
    throw new ApiError(
      res.status,
      formatDetail(detail) ?? (res.statusText || `Request failed (${res.status})`),
    );
  }
  return data as T;
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong";
}
