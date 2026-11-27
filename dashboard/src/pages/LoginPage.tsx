// /login#code=… : exchanges the bot's one-time code for a session (API §6). The code is removed from the address bar
// and history before the request is made.
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, ApiError, errorMessage } from "../api/client";
import type { SessionOut } from "../api/types";
import { saveSession } from "../auth/session";
import { Notice } from "../components/common";
import { hasCodeInLocation, takeCodeFromLocation } from "../lib/login";
import { t } from "../strings";

// One request per code, even when React runs the effect twice in development.
let pending: Promise<SessionOut> | null = null;

type State = { kind: "working" } | { kind: "missing" } | { kind: "error"; message: string };

export function LoginPage() {
  const navigate = useNavigate();
  const [state, setState] = useState<State>(() =>
    pending || hasCodeInLocation() ? { kind: "working" } : { kind: "missing" });

  useEffect(() => {
    if (state.kind !== "working") return;
    let alive = true;
    if (!pending) {
      const code = takeCodeFromLocation();
      if (code) {
        pending = api.post<SessionOut>("/auth/session", { code });
        pending.then(() => undefined, () => undefined).finally(() => { pending = null; });
      }
    }
    const request = pending ?? Promise.reject(new Error(t.login.missing));
    request.then(
      (s) => {
        saveSession(s.token, s.expires_at);
        if (alive) navigate("/alerts", { replace: true });
      },
      (err) => {
        if (alive) setState({ kind: "error", message: err instanceof ApiError ? errorMessage(err) : t.login.missing });
      },
    );
    return () => { alive = false; };
  }, [navigate, state.kind]);

  return (
    <section>
      <h1>{t.login.title}</h1>
      {state.kind === "working" && <p role="status">{t.login.working}</p>}
      {state.kind === "missing" && <Notice>{t.login.missing} <Link to="/alerts">{t.nav.alerts}</Link></Notice>}
      {state.kind === "error" && (
        <Notice tone="error"><p>{state.message}</p><p><Link to="/alerts">{t.nav.alerts}</Link></p></Notice>
      )}
    </section>
  );
}
