// F10: alerts managed from the dashboard after signing in with the bot's one-time /login link (API §6).
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { api, errorMessage } from "../api/client";
import { useAlertHistory, useAlerts, useCommodities, useMarket } from "../api/queries";
import type { AlertIn, AlertOut, ConditionType, MarketItem } from "../api/types";
import { clearSession, useSession } from "../auth/session";
import { ErrorNotice, Loading, Notice } from "../components/common";
import { MarketPicker } from "../components/MarketPicker";
import { formatDateTime } from "../lib/format";
import { t } from "../strings";

const BOT = (import.meta.env.VITE_BOT_USERNAME ?? "").replace(/^@/, "");
const BOT_OK = /^[A-Za-z0-9_]{5,32}$/.test(BOT);

export function AlertsPage() {
  const session = useSession();
  return (
    <section>
      <h1>{t.alerts.title}</h1>
      {session ? <SignedIn expiresAt={session.expiresAt} /> : <SignedOut />}
      <p className="small muted">{t.alerts.privacy}</p>
    </section>
  );
}

function SignedOut() {
  return (
    <div className="card">
      <p>{t.alerts.signedOutIntro}</p>
      <ol>
        <li>{t.alerts.step1}</li>
        <li>{t.alerts.step2}</li>
        <li>{t.alerts.step3}</li>
      </ol>
      {BOT_OK && (
        <p><a className="button" href={`https://t.me/${BOT}`} target="_blank" rel="noopener noreferrer">
          {t.alerts.openBot(BOT)}</a></p>
      )}
    </div>
  );
}

function SignedIn({ expiresAt }: { expiresAt: string }) {
  const qc = useQueryClient();
  const alerts = useAlerts();
  const history = useAlertHistory();
  const [notice, setNotice] = useState<string | null>(null);

  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/alerts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });
  const signOut = useMutation({
    mutationFn: () => api.post<void>("/auth/logout", {}, true),
    onSettled: () => { clearSession(); qc.removeQueries({ queryKey: ["alerts"] });
      qc.removeQueries({ queryKey: ["alert-history"] }); },
  });
  const deleteAll = useMutation({
    mutationFn: () => api.delete("/me"),
    onSuccess: () => { clearSession(); qc.removeQueries({ queryKey: ["alerts"] });
      qc.removeQueries({ queryKey: ["alert-history"] }); },
  });

  return (
    <>
      <p className="small muted">Signed in until {formatDateTime(expiresAt)}.</p>
      {notice && <Notice tone="ok">{notice}</Notice>}

      <div className="card">
        <h2>{t.alerts.mine}</h2>
        {alerts.isPending && <Loading />}
        {alerts.isError && <ErrorNotice error={alerts.error} onRetry={() => alerts.refetch()} />}
        {alerts.data && (alerts.data.length === 0 ? <p className="muted">{t.alerts.none}</p> : (
          <ul className="list" data-testid="alert-list">
            {alerts.data.map((a: AlertOut) => (
              <li key={a.id} className="row between">
                <span>
                  <strong>{a.commodity}, {a.market}</strong><br />
                  <span className="meta">{a.description}{!a.active && " (paused)"}</span>
                </span>
                <button type="button" className="danger" aria-label={t.alerts.deleteAlert(a.id)}
                        disabled={remove.isPending} onClick={() => remove.mutate(a.id)}>{t.alerts.delete}</button>
              </li>
            ))}
          </ul>
        ))}
        {remove.isError && <ErrorNotice error={remove.error} />}
      </div>

      <CreateAlert onCreated={() => { setNotice(t.alerts.created);
        void qc.invalidateQueries({ queryKey: ["alerts"] }); }} />

      <div className="card">
        <h2>{t.alerts.history}</h2>
        {history.isPending && <Loading />}
        {history.isError && <ErrorNotice error={history.error} />}
        {history.data && (history.data.length === 0 ? <p className="muted">{t.alerts.noHistory}</p> : (
          <ul className="list">
            {history.data.map((n) => (
              <li key={n.id}>
                <p className="small muted">{formatDateTime(n.created_at)} · {t.alerts.status[n.status] ?? n.status}
                  {n.sent_at && `, sent ${formatDateTime(n.sent_at)}`}</p>
                <p>{n.message}</p>
              </li>
            ))}
          </ul>
        ))}
      </div>

      <div className="row">
        <button type="button" onClick={() => signOut.mutate()} disabled={signOut.isPending}>{t.alerts.signOut}</button>
        <button type="button" className="danger" disabled={deleteAll.isPending}
                onClick={() => { if (window.confirm(t.alerts.deleteConfirm)) deleteAll.mutate(); }}>
          {t.alerts.deleteAccount}</button>
      </div>
      {deleteAll.isError && <ErrorNotice error={deleteAll.error} />}
    </>
  );
}

function CreateAlert({ onCreated }: { onCreated: () => void }) {
  const ids = { crop: useId(), type: useId(), value: useId() };
  const [params] = useSearchParams();
  const commodities = useCommodities();
  const preCrop = Number(params.get("crop")) || undefined;
  const preMarket = Number(params.get("m")) || 0;
  const pre = useMarket(preMarket);
  const [crop, setCrop] = useState<number | undefined>(preCrop);
  const [picked, setPicked] = useState<MarketItem | null>(null);
  const [type, setType] = useState<ConditionType>("price_above");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const cropId = crop ?? commodities.data?.[0]?.id;
  const market = picked ?? (pre.data && crop === preCrop ? { id: pre.data.id, name: pre.data.name,
    district: pre.data.district, state: pre.data.state } : null);

  const create = useMutation({
    mutationFn: (body: AlertIn) => api.post<AlertOut>("/alerts", body, true),
    onSuccess: () => { setValue(""); setError(null); onCreated(); },
    onError: (e) => setError(errorMessage(e)),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    const v = Number(value);
    if (!cropId || !market) { setError("Choose a crop and a mandi."); return; }
    const max = type === "forecast_change_pct" ? 100 : 1_000_000;
    if (!Number.isFinite(v) || v <= 0 || v > max) {
      setError(type === "forecast_change_pct" ? "Enter a percentage between 0 and 100." : "Enter a price above ₹0.");
      return;
    }
    create.mutate({ commodity_id: cropId, market_id: market.id, condition: { type, value: v } });
  }

  return (
    <form className="card" onSubmit={submit} noValidate>
      <h2>{t.alerts.create}</h2>
      <div className="fields cols-2">
        <div className="field">
          <label htmlFor={ids.crop}>{t.alerts.crop}</label>
          <select id={ids.crop} value={cropId ?? ""} onChange={(e) => { setCrop(Number(e.target.value)); setPicked(null); }}>
            {commodities.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div>
          {market ? (
            <div className="field">
              <span className="small muted">{t.alerts.mandi}</span>
              <p><strong>{market.name}</strong> <span className="muted">{market.state.name}</span>{" "}
                <button type="button" className="link" onClick={() => setPicked(null)}>Change</button></p>
            </div>
          ) : <MarketPicker commodityId={cropId} label={t.alerts.mandi} onPick={setPicked} />}
        </div>
        <div className="field">
          <label htmlFor={ids.type}>{t.alerts.condition}</label>
          <select id={ids.type} value={type} onChange={(e) => setType(e.target.value as ConditionType)}>
            <option value="price_above">{t.alerts.above}</option>
            <option value="price_below">{t.alerts.below}</option>
            <option value="forecast_change_pct">{t.alerts.forecastMove}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor={ids.value}>
            {t.alerts.value} ({type === "forecast_change_pct" ? t.alerts.pctUnit : t.alerts.rupeesUnit})</label>
          <input id={ids.value} inputMode="decimal" value={value} maxLength={12}
                 onChange={(e) => setValue(e.target.value.replace(/[^0-9.]/g, ""))} />
        </div>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <button type="submit" className="primary" disabled={create.isPending}>{t.alerts.save}</button>
      <p className="small muted">
        Price alerts fire once when a newly reported price crosses the value. Forecast alerts fire when a weekly 7-day
        forecast differs from the last reported price by at least the percentage. Forecasts are estimates, not
        guarantees.{market && preMarket === market.id && pre.data && ` Mandi pre-filled from ${pre.data.name}.`}
      </p>
    </form>
  );
}
