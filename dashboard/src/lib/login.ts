// Reading the one-time sign-in code from /login#code=… (the fragment is never sent to a server by the browser).

const CODE_RE = /(?:^#|&)code=([^&]*)/;

/** Whether the address carries a sign-in code (no side effects). */
export function hasCodeInLocation(): boolean {
  return CODE_RE.test(window.location.hash);
}

/** Returns the code and removes it from the address bar and the history entry. */
export function takeCodeFromLocation(): string | null {
  const match = CODE_RE.exec(window.location.hash);
  if (!match) return null;
  window.history.replaceState(null, "", window.location.pathname);
  try {
    const code = decodeURIComponent(match[1] ?? "");
    return code || null;
  } catch {
    return null;
  }
}
