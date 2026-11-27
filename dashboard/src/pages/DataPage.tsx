// F7 and F9: freshness, the models in use, their hold-out accuracy against the last price, and live monitoring.
import { useEvaluation, useFreshness, useModels, useMonitoring } from "../api/queries";
import type { HorizonScore, ModelSummary } from "../api/types";
import { ErrorNotice, Loading, Notice, SourceNote } from "../components/common";
import { formatDate, formatDateTime, pct } from "../lib/format";
import { t } from "../strings";

interface LiveRow {
  day_range?: string; origins?: number; points?: number; wape_model?: number | null; wape_last_value?: number | null;
  coverage80?: number | null; coverage90?: number | null; as_of?: string;
}

function HoldoutRows({ model }: { model: ModelSummary }) {
  const ev = useEvaluation(model.version);
  if (ev.isPending) return <tr><td colSpan={7}>{model.commodity}: {t.loading}</td></tr>;
  if (ev.isError) return <tr><td colSpan={7}>{model.commodity}: could not load the evaluation.</td></tr>;
  const served = ev.data.evaluation.served ?? [];
  const lv = ev.data.evaluation.last_value ?? [];
  return (
    <>
      {served.map((s: HorizonScore) => {
        const base = lv.find((x) => x.h === s.h);
        const better = base ? s.wape < base.wape : null;
        return (
          <tr key={`${model.version}-${s.h}`}>
            <td>{model.commodity}</td><td className="num">{s.h}</td>
            <td className="num">{pct(s.wape)}</td><td className="num">{base ? pct(base.wape) : "—"}</td>
            <td className={better == null ? "" : better ? "yes" : "no"}>{better == null ? "—" : better ? "Yes" : "No"}</td>
            <td className="num">{s.coverage80 != null ? pct(s.coverage80) : "—"} /{" "}
              {s.coverage90 != null ? pct(s.coverage90) : "—"}</td>
            <td className="num">{s.dir_acc_moves_ge_2pct != null ? pct(s.dir_acc_moves_ge_2pct) : "—"}</td>
          </tr>
        );
      })}
    </>
  );
}

export function DataPage() {
  const freshness = useFreshness();
  const models = useModels();
  const monitoring = useMonitoring();

  return (
    <section>
      <h1>{t.data.title}</h1>

      <div className="card">
        <h2>{t.data.freshness}</h2>
        {freshness.isPending && <Loading />}
        {freshness.isError && <ErrorNotice error={freshness.error} onRetry={() => freshness.refetch()} />}
        {freshness.data && (
          <>
            <p className="small muted">
              {freshness.data.last_successful_ingestion &&
                <>Last data update: {formatDateTime(String(freshness.data.last_successful_ingestion.finished_at))}. </>}
              {freshness.data.last_forecast_publication &&
                <>Last forecast publication:{" "}
                  {formatDateTime(String(freshness.data.last_forecast_publication.finished_at))}.</>}
            </p>
            <div className="table-wrap">
              <table data-testid="freshness-table">
                <thead><tr><th>Crop</th><th>Latest report</th><th>Latest complete day</th>
                  <th className="num">Mandis reporting (14 days)</th><th className="num">Stopped reporting</th>
                  <th className="num">With forecasts</th><th>Forecast issued</th></tr></thead>
                <tbody>
                  {freshness.data.commodities.map((c) => (
                    <tr key={c.commodity_id}>
                      <td>{c.commodity}</td>
                      <td>{c.latest_arrival_date ? formatDate(c.latest_arrival_date) : "—"}</td>
                      <td>{c.latest_complete_date ? formatDate(c.latest_complete_date) : "—"}</td>
                      <td className="num">{c.reported_last_14_days}</td><td className="num">{c.stopped_reporting}</td>
                      <td className="num">{c.eligible_series}</td>
                      <td>{c.latest_forecast_origin ? formatDate(c.latest_forecast_origin) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="small muted">“Stopped reporting”: last report 15–90 days before the latest date. Only
              mandis with enough recent, regular reports get forecasts.</p>
          </>
        )}
      </div>

      <div className="card">
        <h2>{t.data.holdout}</h2>
        <p className="small">{t.data.holdoutNote}</p>
        {models.isPending && <Loading />}
        {models.isError && <ErrorNotice error={models.error} onRetry={() => models.refetch()} />}
        {models.data && (
          <div className="table-wrap">
            <table data-testid="holdout-table">
              <thead><tr><th>Crop</th><th className="num">Days ahead</th><th className="num">Forecast error</th>
                <th className="num">Last-price error</th><th>Better?</th><th className="num">In 80% / 90% range</th>
                <th className="num">Direction right (moves ≥ 2%)</th></tr></thead>
              <tbody>{models.data.map((m) => <HoldoutRows key={m.version} model={m} />)}</tbody>
            </table>
          </div>
        )}
        <p className="small muted">Error is WAPE: total absolute error as a share of total price. The ranges should
          hold the actual price at least 80% and 90% of the time; values below that mean the ranges are too narrow
          (the full report explains why Soyabean's are).</p>
      </div>

      <div className="card">
        <h2>{t.data.models}</h2>
        {models.data && (
          <div className="table-wrap">
            <table data-testid="models-table">
              <thead><tr><th>Crop</th><th>Version</th><th>Trained on data up to</th><th>Used until</th>
                <th>Promotion check</th><th>Latest forecast</th></tr></thead>
              <tbody>
                {models.data.map((m) => (
                  <tr key={m.version}>
                    <td>{m.commodity}</td><td className="small">{m.version}</td>
                    <td>{formatDate(m.training_cutoff)}</td><td>{formatDate(m.valid_to)}</td>
                    <td className={m.gate_passed ? "yes" : m.gate_passed === false ? "no" : ""}>
                      {m.gate_passed == null ? "—" : m.gate_passed ? "Passed" : "Failed"}</td>
                    <td>{m.latest_forecast_origin ? formatDate(m.latest_forecast_origin) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h2>{t.data.live}</h2>
        <p className="small">{t.data.liveNote}</p>
        {monitoring.isPending && <Loading />}
        {monitoring.isError && <ErrorNotice error={monitoring.error} onRetry={() => monitoring.refetch()} />}
        {monitoring.data && (
          <>
            <div className="table-wrap">
              <table data-testid="live-table">
                <thead><tr><th>Crop</th><th>Days</th><th className="num">Weekly issues</th>
                  <th className="num">Forecasts scored</th><th className="num">Model error</th>
                  <th className="num">Last-price error</th><th className="num">In 80% / 90% range</th>
                  <th>Reliable yet?</th></tr></thead>
                <tbody>
                  {monitoring.data.commodities.flatMap((c) => (c.live as LiveRow[]).map((l) => {
                    const reliable = (l.points ?? 0) >= 500 && (l.origins ?? 0) >= 4;
                    return (
                      <tr key={`${c.commodity_id}-${l.day_range}`}>
                        <td>{c.commodity}</td><td>{l.day_range}</td><td className="num">{l.origins}</td>
                        <td className="num">{l.points}</td>
                        <td className="num">{l.wape_model != null ? pct(l.wape_model) : "—"}</td>
                        <td className="num">{l.wape_last_value != null ? pct(l.wape_last_value) : "—"}</td>
                        <td className="num">{l.coverage80 != null ? pct(l.coverage80) : "—"} /{" "}
                          {l.coverage90 != null ? pct(l.coverage90) : "—"}</td>
                        <td className={reliable ? "" : "no"}>{reliable ? "Yes" : "Not yet"}</td>
                      </tr>
                    );
                  }))}
                </tbody>
              </table>
            </div>
            <p className="small muted">Reliable means at least 500 scored forecasts from at least 4 weekly issues.
              {monitoring.data.checked_at && <> Checked {formatDateTime(monitoring.data.checked_at)}.</>}</p>
            {(monitoring.data.global_warnings.length > 0
              || monitoring.data.commodities.some((c) => c.warnings.length > 0)) && (
              <Notice tone="warning" title="Monitoring warnings">
                <ul>
                  {[...monitoring.data.global_warnings.map((w) => ({ who: "All crops", w })),
                    ...monitoring.data.commodities.flatMap((c) => c.warnings.map((w) => ({ who: c.commodity, w })))]
                    .map(({ who, w }, i) => {
                      const x = w as { check?: string; detail?: string };
                      return <li key={i}>{who}: {x.check ?? "check"} failed{x.detail ? ` (${x.detail})` : ""}</li>;
                    })}
                </ul>
              </Notice>
            )}
          </>
        )}
      </div>
      <SourceNote source={freshness.data?.source} />
    </section>
  );
}
