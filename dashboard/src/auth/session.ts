// The dashboard session: a Bearer token from POST /v1/auth/session, kept with its expiry in localStorage
// (docs/14 §2 and §4). Expired or rejected tokens are removed.
import { useSyncExternalStore } from "react";

const KEY = "mandicast.session";
const listeners = new Set<() => void>();

export interface Session {
  token: string;
  expiresAt: string;
}

function read(): Session | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Partial<Session>;
    if (typeof s.token !== "string" || typeof s.expiresAt !== "string") return null;
    if (!(Date.parse(s.expiresAt) > Date.now())) return null;
    return { token: s.token, expiresAt: s.expiresAt };
  } catch {
    return null;
  }
}

let cached: Session | null | undefined;

function notify() {
  cached = undefined;
  for (const l of listeners) l();
}

export function getSession(): Session | null {
  if (cached === undefined) {
    cached = read();
    if (cached === null) window.localStorage.removeItem(KEY);
  }
  return cached;
}

export function saveSession(token: string, expiresAt: string): void {
  window.localStorage.setItem(KEY, JSON.stringify({ token, expiresAt }));
  notify();
}

export function clearSession(): void {
  window.localStorage.removeItem(KEY);
  notify();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) notify();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useSession(): Session | null {
  return useSyncExternalStore(subscribe, getSession, () => null);
}
