// Minimal typed client for the MandiCast API (docs/07). Every request has a timeout, sends no cookies, parses the
// API's single error format, and attaches the Bearer token only to the configured API origin.
import { clearSession, getSession } from "../auth/session";

const CONFIGURED_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";
/** `same-origin`: the web server that serves the dashboard also proxies /v1 to the API (Docker Compose, docs/15). */
export const API_BASE = CONFIGURED_BASE === "same-origin" ? "" : CONFIGURED_BASE.replace(/\/+$/, "");
const TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly retryAfterS: number | null;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {},
              retryAfterS: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryAfterS = retryAfterS;
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>;

interface RequestOptions {
  query?: Query;
  body?: unknown;
  auth?: boolean;
  signal?: AbortSignal;
}

export function buildUrl(path: string, query?: Query): string {
  const url = new URL(`${API_BASE}/v1${path}`, window.location.origin);
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    url.searchParams.set(k, String(v));
  }
  return url.toString();
}

async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.auth) {
    const session = getSession();
    if (!session) throw new ApiError(401, "auth_required", "Sign in with a link from the MandiCast Telegram bot.");
    headers.Authorization = `Bearer ${session.token}`;
  }
  const timeout = AbortSignal.timeout(TIMEOUT_MS);
  const signal = opts.signal ? AbortSignal.any([opts.signal, timeout]) : timeout;
  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method, headers, signal, credentials: "omit", referrerPolicy: "no-referrer", mode: "cors",
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch (err) {
    if (opts.signal?.aborted) throw err;
    if (timeout.aborted) throw new ApiError(0, "timeout", "The server took too long to answer. Try again.");
    throw new ApiError(0, "network_error", "Could not reach the MandiCast server. Check your connection.");
  }
  if (res.status === 204) return undefined as T;
  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }
  if (!res.ok) {
    const e = (payload as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })?.error;
    const retry = Number(res.headers.get("Retry-After"));
    if (res.status === 401 && opts.auth) clearSession();
    throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? `The server answered with HTTP ${res.status}.`,
                       e?.details ?? {}, Number.isFinite(retry) && retry > 0 ? retry : null);
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal, auth = false) =>
    request<T>("GET", path, { query, signal, auth }),
  post: <T>(path: string, body: unknown, auth = false) => request<T>("POST", path, { body, auth }),
  delete: (path: string) => request<void>("DELETE", path, { auth: true }),
};

/** A message suitable for the page, for any thrown value. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      return `Too many requests. Try again in ${err.retryAfterS ?? 60} seconds.`;
    }
    return err.message;
  }
  return "Something went wrong. Try again.";
}
