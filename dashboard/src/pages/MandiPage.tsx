// One mandi and crop: F2 history, F3 latest price, F5 forecast with ranges, F6 "not enough data", F7 accuracy,
// F8 trends, F1 names as reported.
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import { useEligibility, useForecast, useMarket, usePrices, useTrends } from "../api/queries";
import type {
  Accuracy, EligibilityResponse, ForecastResponse, LiveAccuracy, MarketCommodity, PriceItem, TrendsResponse,
} from "../api/types";
import { CropChips, ErrorNotice, Loading, Notice, Segmented, SourceNote } from "../components/common";
import { PriceChart, PriceLegend } from "../components/PriceChart";
import { forecastPoints } from "../lib/forecast";
import { dayNumber } from "../lib/chart";
import {
  addDays, daysBetween, formatDate, inr, inrPerKg, int, pct, relativeDays, sentence, signedPct, todayIso,
} from "../lib/format";
import { t } from "../strings";

// 1 month by default: on a phone the 7 or 14 forecast days then take a readable share of the chart width.
const PERIODS = [
  { value: 35, label: "1M" }, { value: 91, label: "3M" }, { value: 365, label: "1Y" }, { value: 1095, label: "3Y" },
];

export function MandiPage() {
  const marketId = Number(useParams().marketId);
  const [params, setParams] = useSearchParams();
  const market = useMarket(marketId);

  if (!Number.isInteger(marketId) || marketId < 1) return <Notice tone="error">{t.notFound}</Notice>;
  if (market.isPending) return <Loading />;
  if (market.isError) return <ErrorNotice error={market.error} onRetry={() => market.refetch()} />;

  const m = market.data;
  const wanted = Number(params.get("crop"));
  const series = m.commodities.find((c) => c.commodity_id === wanted)
    ?? m.commodities.find((c) => c.eligible_for_forecast) ?? m.commodities[0];
  const setCrop = (id: number) => {
    const next = new URLSearchParams(params);
    next.set("crop", String(id));
    setParams(next, { replace: true });
  };

  return (
    <section>
      <h1>{m.name}</h1>
      <p className="muted">{m.district.name}, {m.state.name}</p>
      {m.commodities.length > 1 && (
        <div className="field">
          <CropChips label={t.mandi.otherCrops}
                     commodities={m.commodities.map((c) => ({ id: c.commodity_id, name: c.commodity }))}
                     selected={series?.commodity_id} onSelect={setCrop} />
        </div>
      )}
      {series ? <SeriesView key={series.commodity_id} marketId={marketId} series={series} />
        : <Notice>No prices are recorded for this mandi.</Notice>}

      {m.aliases.length > 0 && (
        <details className="card">
          <summary>{t.mandi.names}</summary>
          <ul>
            {m.aliases.map((a) => (
              <li key={`${a.name_as_reported}|${a.rule}`}>
                “{a.name_as_reported}” <span className="muted small">
                  (linked by rule “{a.rule}”{a.last_seen && `, last seen ${formatDate(a.last_seen)}`})</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function SeriesView({ marketId, series }: { marketId: number; series: MarketCommodity }) {
  const cid = series.commodity_id;
  const [horizon, setHorizon] = useState<7 | 14>(7);
  const [level, setLevel] = useState<0.8 | 0.9>(0.8);
  const [period, setPeriod] = useState(35);
  const [view, setView] = useState<"chart" | "table">("chart");
  const [showMinMax, setShowMinMax] = useState(false);

  const lastDate = series.last_date ?? undefined;
  const from = lastDate ? addDays(lastDate, -period) : undefined;
  const prices = usePrices(cid, marketId, from, lastDate);
  const trends = useTrends(cid, marketId);
  const forecast = useForecast(cid, marketId, horizon);
  const forecastMissing = forecast.error instanceof ApiError && forecast.error.status === 422;
  const eligibility = useEligibility(cid, marketId, forecastMissing || !series.eligible_for_forecast);
  const fc = forecast.isSuccess ? forecast.data : null;

  return (
    <>
      <div className="grid-2">
        <LatestCard trends={trends.data} pending={trends.isPending} error={trends.error} />
        <TrendsCard trends={trends.data} />
      </div>

      <div className="card">
        <h2>{t.mandi.forecastTitle}: {series.commodity}</h2>
        <div className="controls">
          <Segmented label="Days ahead" value={horizon} onChange={setHorizon}
                     options={[{ value: 7, label: t.mandi.days(7) }, { value: 14, label: t.mandi.days(14) }]} />
          <Segmented label="Range" value={level} onChange={setLevel}
                     options={[{ value: 0.8, label: "80%" }, { value: 0.9, label: "90%" }]} />
        </div>
        {forecast.isPending && <Loading />}
        {forecastMissing && <NotEnoughData eligibility={eligibility.data} message={forecast.error?.message ?? ""} />}
        {forecast.isError && !forecastMissing && (
          forecast.error instanceof ApiError && forecast.error.status === 503
            ? <Notice>{t.mandi.noModel}</Notice>
            : <ErrorNotice error={forecast.error} onRetry={() => forecast.refetch()} />
        )}
        {fc && <ForecastSummary forecast={fc} level={level} />}

        <div className="controls">
          <Segmented label={t.chart.period} value={period} onChange={setPeriod} options={PERIODS} />
          <label className="check">
            <input type="checkbox" checked={showMinMax} onChange={(e) => setShowMinMax(e.target.checked)} />
            {t.chart.showMinMax}
          </label>
          <button type="button" onClick={() => setView(view === "chart" ? "table" : "chart")}>
            {view === "chart" ? t.chart.showTable : t.chart.showChart}
          </button>
        </div>
        {prices.isPending && <Loading />}
        {prices.isError && <ErrorNotice error={prices.error} onRetry={() => prices.refetch()} />}
        {prices.data && from && (
          <div className={prices.isPlaceholderData || forecast.isPlaceholderData ? "refetching" : undefined}>
            {view === "chart" ? (
              <>
                <PriceLegend showMinMax={showMinMax} hasForecast={!!fc} level={level} />
                <PriceChart history={prices.data.items} forecast={fc} level={level} showMinMax={showMinMax}
                            startDay={dayNumber(from)}
                            label={`${series.commodity} modal price at ${prices.data.series.market}, ` +
                              `${formatDate(from)} to ${formatDate(lastDate ?? from)}` +
                              (fc ? `, with the forecast issued ${formatDate(fc.issued_on)}` : "") +
                              ". Use the arrow keys to read values; a table view is available."} />
                <p className="chart-note">{t.chart.gapNote}</p>
              </>
            ) : (
              <>
                {fc && <ForecastTable forecast={fc} />}
                <PriceTable rows={prices.data.items} />
              </>
            )}
          </div>
        )}
      </div>

      {fc?.accuracy && <AccuracyCard accuracy={fc.accuracy} live={fc.live_accuracy} crop={series.commodity}
                                     horizon={horizon} />}

      <p className="row">
        <Link className="button" to={`/compare?crop=${cid}&m=${marketId}`}>{t.mandi.compare}</Link>
        <Link className="button" to={`/alerts?crop=${cid}&m=${marketId}`}>{t.mandi.alert}</Link>
      </p>
      <SourceNote source={prices.data?.source ?? trends.data?.source}
                  asOf={prices.data?.data_as_of ?? trends.data?.data_as_of} />
    </>
  );
}

function LatestCard({ trends, pending, error }: { trends?: TrendsResponse; pending: boolean; error: unknown }) {
  if (pending) return <div className="card"><Loading /></div>;
  if (error) return <div className="card"><ErrorNotice error={error} /></div>;
  const latest = trends?.latest;
  if (!latest) return <div className="card"><p>No usable reports for this crop at this mandi.</p></div>;
  const age = daysBetween(latest.date, todayIso());
  return (
    <div className="card" data-testid="latest-card">
      <h2 className="stat-label">{t.mandi.latest}</h2>
      <p className="hero-price">{inr(latest.modal_price)}</p>
      <p className="hero-sub">{t.mandi.perQuintal} · {inrPerKg(latest.modal_price)} {t.mandi.perKg}</p>
      <p>
        <strong>{formatDate(latest.date)}</strong> <span className="muted">({relativeDays(age)})</span>
        {latest.min_price != null && latest.max_price != null && (
          <><br /><span className="muted">{t.mandi.range}: {inr(latest.min_price)}–{inr(latest.max_price)}</span></>
        )}
        <br /><span className="muted small">{t.mandi.varietyGrade(latest.variety, latest.grade)}</span>
      </p>
      {age > 14 && <Notice tone="warning">{t.mandi.stale(int(age))}</Notice>}
    </div>
  );
}

function TrendsCard({ trends }: { trends?: TrendsResponse }) {
  if (!trends?.latest) return null;
  const p = trends.latest.modal_price;
  const lines: string[] = [];
  const w7 = trends.vs_previous_7_days;
  const w30 = trends.vs_previous_30_days;
  const describe = (c: number) => (Math.abs(c) < 0.05 ? "level with" : c > 0 ? `${signedPct(c).slice(1)} above`
    : `${signedPct(c).slice(1)} below`);
  const reports = (n: number) => `${int(n)} ${n === 1 ? "report" : "reports"}`;
  if (w7.change_pct != null && w7.average != null) {
    lines.push(`${inr(p)} is ${describe(w7.change_pct)} the average of the previous 7 days (${inr(w7.average)}, ` +
      `${reports(w7.reports)}).`);
  }
  if (w30.change_pct != null && w30.average != null) {
    lines.push(`${inr(p)} is ${describe(w30.change_pct)} the average of the previous 30 days ` +
      `(${inr(w30.average)}, ${reports(w30.reports)}).`);
  }
  const r = trends.range_52_weeks;
  if (r.low != null && r.high != null && r.position_pct != null) {
    lines.push(`In the last 52 weeks prices ranged from ${inr(r.low)} to ${inr(r.high)}; the latest is ` +
      `${Math.round(r.position_pct)}% of the way up that range.`);
  }
  if (trends.volatility_91_days_pct != null) {
    lines.push(`Typical change between reports over 91 days: ${trends.volatility_91_days_pct.toFixed(1)}%.`);
  }
  return (
    <div className="card" data-testid="trends-card">
      <h2>{t.mandi.trendsTitle}</h2>
      <ul>{lines.map((l) => <li key={l}>{l}</li>)}</ul>
      <p className="small muted">{trends.note}</p>
    </div>
  );
}

function ForecastSummary({ forecast, level }: { forecast: ForecastResponse; level: 0.8 | 0.9 }) {
  const pts = forecastPoints(forecast);
  const show = pts.filter((p) => p.h === 7 || p.h === 14);
  const lastValueDays = forecast.points.filter((p) => p.served_by === "last_value").length;
  const lp = Math.round(level * 100);
  return (
    <div data-testid="forecast-summary">
      <div className="forecast-headline">
        {show.map((p) => {
          const lo = level === 0.9 ? p.l90 : p.l80;
          const hi = level === 0.9 ? p.u90 : p.u80;
          return (
            <div key={p.h}>
              <div className="stat-label">{t.mandi.dayHeadline(p.h, formatDate(p.date))}</div>
              <div className="stat-value">{inr(p.y)}</div>
              {lo != null && hi != null && (
                <div className="small muted">{t.mandi.likelyRange(lp)}: {inr(lo)}–{inr(hi)}</div>
              )}
            </div>
          );
        })}
      </div>
      <p className="small muted">{t.mandi.issued(formatDate(forecast.issued_on),
        formatDate(String((forecast.last_observed as { date?: string }).date ?? forecast.issued_on)))}
        {forecast.model ? ` Model ${forecast.model.version}.` : ""}</p>
      {lastValueDays > 0 && <Notice tone="warning">{t.mandi.servedByLastValue}</Notice>}
      {forecast.warnings.length > 0 && (
        <Notice tone="warning" title="Read before using this forecast">
          <ul>{forecast.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </Notice>
      )}
      <p className="disclaimer">{forecast.disclaimer}</p>
    </div>
  );
}

function NotEnoughData({ eligibility, message }: { eligibility?: EligibilityResponse; message: string }) {
  const rules = (eligibility?.rules ?? {}) as Record<string, number>;
  const stats = (eligibility?.stats ?? {}) as Record<string, number | null>;
  const fmt = (v: number | null | undefined, f: (x: number) => string) => (v == null ? "none" : f(v));
  return (
    <Notice tone="info" title={t.mandi.notEnoughTitle}>
      <p>{eligibility?.reason ? `Reason: ${eligibility.reason}.` : message}</p>
      {eligibility && (
        <>
          <p>{t.mandi.notEnoughRules}</p>
          <div className="table-wrap">
            <table data-testid="eligibility-table">
              <thead><tr><th className="wrap">Rule</th><th className="num">Needed</th><th className="num">This mandi</th></tr></thead>
              <tbody>
                <tr><td className="wrap">Days since the last report</td><td className="num">≤ {rules.max_days_since_last}</td>
                  <td className="num">{fmt(stats.days_since_last_report, String)}</td></tr>
                <tr><td className="wrap">Report days in the last 2 years</td>
                  <td className="num">≥ {rules.min_report_days_in_last_730}</td>
                  <td className="num">{fmt(stats.report_days_last_730, String)}</td></tr>
                <tr><td className="wrap">Median gap between reports (last year)</td>
                  <td className="num">≤ {rules.max_median_gap_last_365} days</td>
                  <td className="num">{fmt(stats.median_gap_days_last_365, (x) => `${x} days`)}</td></tr>
                <tr><td className="wrap">Share of reports flagged as errors (last year)</td>
                  <td className="num">≤ {pct(rules.max_flagged_share_last_365 ?? 0, 0)}</td>
                  <td className="num">{fmt(stats.flagged_share_last_365, (x) => pct(x))}</td></tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </Notice>
  );
}

function AccuracyCard({ accuracy, live, crop, horizon }: {
  accuracy: Accuracy; live: LiveAccuracy | null; crop: string; horizon: number;
}) {
  const cov = (accuracy.interval_coverage ?? {}) as Record<string, number>;
  const byModel = accuracy.method === "model";
  return (
    <div className="card" data-testid="accuracy-card">
      <h2>{t.mandi.accuracyTitle}</h2>
      <p>
        On a hold-out year that the model never saw, {crop} forecasts {horizon} days ahead
        {byModel ? "" : " (made with the last reported price)"} were off by{" "}
        <strong>{pct(accuracy.wape)}</strong> of the price on average. The simple rule “the price stays at its last
        report” was off by <strong>{pct(accuracy.baseline_wape)}</strong>.
      </p>
      <div className="stats">
        <div><div className="stat-label">Better than the last price?</div>
          <div className={`stat-value ${accuracy.beats_baseline ? "yes" : "no"}`}>
            {accuracy.beats_baseline == null ? "—" : accuracy.beats_baseline ? "Yes" : "No"}</div></div>
        {accuracy.direction_accuracy_moves_ge_2pct != null && (
          <div><div className="stat-label">Direction right when the price moved ≥ 2%</div>
            <div className="stat-value">{pct(accuracy.direction_accuracy_moves_ge_2pct)}</div></div>
        )}
        {cov["0.8"] != null && (
          <div><div className="stat-label">Actual price inside the 80% range</div>
            <div className="stat-value">{pct(cov["0.8"])}</div></div>
        )}
        {cov["0.9"] != null && (
          <div><div className="stat-label">Actual price inside the 90% range</div>
            <div className="stat-value">{pct(cov["0.9"])}</div></div>
        )}
      </div>
      <p className="small muted">
        {accuracy.evaluated_on}; {accuracy.points.toLocaleString("en-IN")} forecasts of this crop across all its mandis.
        A range that holds the actual price less often than its level (for example below 80% for the 80% range) is
        too narrow.
      </p>
      {live && (
        <p className="small" data-testid="live-accuracy">
          <strong>Live since publication</strong> (days {live.day_range}, {live.points} forecasts from {live.origins}{" "}
          weekly {live.origins === 1 ? "issue" : "issues"}):{" "}
          {live.wape_served != null && <>off by {pct(live.wape_served)}</>}
          {live.wape_last_value != null && <> vs {pct(live.wape_last_value)} for the last price</>}
          {live.coverage80 != null && <>; 80% range held the price {pct(live.coverage80)} of the time</>}.{" "}
          {live.note && sentence(live.note)}
        </p>
      )}
      <p className="small"><Link to="/data">How the models are tested</Link></p>
    </div>
  );
}

function ForecastTable({ forecast }: { forecast: ForecastResponse }) {
  return (
    <div className="table-wrap mb">
      <table data-testid="forecast-table">
        <caption>{t.chart.forecast} issued {formatDate(forecast.issued_on)} (₹ per quintal)</caption>
        <thead>
          <tr><th>Date</th><th className="num">Day</th><th className="num">Forecast</th><th className="num">80% range</th>
            <th className="num">90% range</th><th className="num">Reported</th></tr>
        </thead>
        <tbody>
          {forecastPoints(forecast).map((p, i) => (
            <tr key={p.date}>
              <td>{formatDate(p.date)}</td><td className="num">{p.h}</td><td className="num">{inr(p.y)}</td>
              <td className="num">{p.l80 != null && p.u80 != null ? `${inr(p.l80)}–${inr(p.u80)}` : "—"}</td>
              <td className="num">{p.l90 != null && p.u90 != null ? `${inr(p.l90)}–${inr(p.u90)}` : "—"}</td>
              <td className="num">{forecast.points[i]?.actual_modal_price != null
                ? inr(forecast.points[i]!.actual_modal_price!) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PriceTable({ rows }: { rows: PriceItem[] }) {
  const sorted = [...rows].sort((a, b) => (a.date < b.date ? 1 : -1));
  return (
    <div className="table-wrap">
      <table data-testid="price-table">
        <caption>{t.mandi.tableTitle} (₹ per quintal). {t.mandi.tableNote}</caption>
        <thead>
          <tr><th>Date</th><th className="num">Modal</th><th className="num">Min</th><th className="num">Max</th>
            <th>Variety</th><th>Grade</th><th>Flags</th></tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.source_observation_id}>
              <td>{formatDate(r.date)}</td><td className="num">{inr(r.modal_price)}</td>
              <td className="num">{r.min_price != null ? inr(r.min_price) : "—"}</td>
              <td className="num">{r.max_price != null ? inr(r.max_price) : "—"}</td>
              <td>{r.variety}</td><td>{r.grade}</td><td className="small">{r.flags.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
