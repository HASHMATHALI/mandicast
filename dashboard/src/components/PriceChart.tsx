// History, forecast and forecast ranges of one series in one SVG (docs/14 §3).
// Reported prices: solid blue line (joined only across gaps of at most 3 days) with dots.
// Forecast: dashed orange line from the last input price. Ranges: orange washes (80% stronger, 90% lighter).
// A crosshair snaps to the nearest date; the same readout is available from the keyboard and in the table view.
import { useId, useMemo, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { ForecastResponse, PriceItem } from "../api/types";
import {
  axisRupees, bandPath, dateTicks, dayNumber, isoFromDay, linearScale, linePath, monthTickLabel, nearestIndex,
  niceTicks, splitRuns, WEEKLY_TICKS_MAX_SPAN,
} from "../lib/chart";
import { formatDate, formatShortDate, inr } from "../lib/format";
import { t } from "../strings";
import { forecastPoints, lastObserved, MAX_GAP_DAYS } from "../lib/forecast";
import { useElementWidth } from "../lib/hooks";

const HEIGHT = 250;
const M = { top: 24, right: 14, bottom: 28, left: 46 };

interface HistPoint {
  x: number; y: number; min: number | null; max: number | null; date: string;
}
export interface PriceChartProps {
  history: PriceItem[];
  forecast?: ForecastResponse | null;
  level: 0.8 | 0.9;
  showMinMax: boolean;
  startDay: number;
  label: string;
}

export function PriceChart({ history, forecast, level, showMinMax, startDay, label }: PriceChartProps) {
  const [wrapRef, width] = useElementWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const readoutId = useId();

  const model = useMemo(() => {
    const hist: HistPoint[] = history
      .map((r) => ({ x: dayNumber(r.date), y: r.modal_price, min: r.min_price, max: r.max_price, date: r.date }))
      .filter((p) => p.x >= startDay)
      .sort((a, b) => a.x - b.x);
    const fc = forecast ? forecastPoints(forecast) : [];
    const lo = forecast ? lastObserved(forecast) : null;
    const issueDay = forecast ? dayNumber(forecast.issued_on) : null;
    const start = lo && fc.length ? { x: dayNumber(lo.date), y: lo.price } : null;

    const ys: number[] = hist.map((p) => p.y);
    if (showMinMax) for (const p of hist) { if (p.min != null) ys.push(p.min); if (p.max != null) ys.push(p.max); }
    for (const p of fc) {
      ys.push(p.y);
      const lower = level === 0.9 ? p.l90 : p.l80;
      const upper = level === 0.9 ? p.u90 : p.u80;
      if (lower != null) ys.push(lower);
      if (upper != null) ys.push(upper);
    }
    if (start) ys.push(start.y);
    const xs = [...hist.map((p) => p.x), ...fc.map((p) => p.x)];
    const xMax = Math.max(startDay + 7, ...xs);
    const yMin = ys.length ? Math.min(...ys) : 0;
    const yMax = ys.length ? Math.max(...ys) : 1;
    const pad = (yMax - yMin) * 0.06 || yMax * 0.05 || 1;
    const yTicks = niceTicks(Math.max(0, yMin - pad), yMax + pad, 5);
    const days = [...new Set(xs)].sort((a, b) => a - b);
    return { hist, fc, start, issueDay, xMax, yTicks, days };
  }, [history, forecast, level, showMinMax, startDay]);

  const plotW = Math.max(120, width - M.left - M.right);
  const plotH = HEIGHT - M.top - M.bottom;
  // A little room after the last date so the final forecast point and its range are not drawn against the edge.
  const rightPad = Math.max(1.5, (model.xMax - startDay) * 0.03);
  const sx = linearScale([startDay, model.xMax + rightPad], [M.left, M.left + plotW]);
  const yLo = model.yTicks[0] ?? 0;
  const yHi = model.yTicks[model.yTicks.length - 1] ?? 1;
  const sy = linearScale([yLo, yHi], [M.top + plotH, M.top]);
  const xTicks = dateTicks(startDay, model.xMax, Math.max(2, Math.floor(plotW / 72)));
  const runs = splitRuns(model.hist, MAX_GAP_DAYS);
  const showDots = model.hist.length <= 130;
  const last = model.hist[model.hist.length - 1];
  const fcLast = model.fc[model.fc.length - 1];

  const bandFor = (lv: 0.8 | 0.9) => {
    if (!model.fc.length) return "";
    const pts = model.fc
      .map((p) => ({ x: p.x, lower: (lv === 0.9 ? p.l90 : p.l80) ?? p.y, upper: (lv === 0.9 ? p.u90 : p.u80) ?? p.y }));
    if (model.start) pts.unshift({ x: model.start.x, lower: model.start.y, upper: model.start.y });
    return bandPath(pts, sx, sy);
  };

  const activeDay = active != null ? model.days[active] : undefined;
  const activeHist = activeDay != null ? model.hist.find((p) => p.x === activeDay) : undefined;
  const activeFc = activeDay != null ? model.fc.find((p) => p.x === activeDay) : undefined;

  function onPointer(e: PointerEvent<SVGRectElement>) {
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    setActive(nearestIndex(model.days, sx.invert(px)));
  }

  function onKey(e: KeyboardEvent<SVGSVGElement>) {
    if (!model.days.length) return;
    const lastIdx = model.days.length - 1;
    const cur = active ?? model.hist.length - 1;
    const moves: Record<string, number | null> = {
      ArrowLeft: Math.max(0, cur - 1), ArrowRight: Math.min(lastIdx, cur + 1), Home: 0, End: lastIdx, Escape: null,
    };
    if (!(e.key in moves)) return;
    const next = moves[e.key] ?? null;
    e.preventDefault();
    setActive(next);
  }

  const readout = activeDay == null ? "" : [
    formatDate(isoFromDay(activeDay)),
    activeHist ? `${t.chart.reported} ${inr(activeHist.y)}` +
      (activeHist.min != null && activeHist.max != null ? ` (min ${inr(activeHist.min)}, max ${inr(activeHist.max)})` : "")
      : "",
    activeFc ? `${t.chart.forecast} day ${activeFc.h}: ${inr(activeFc.y)}` +
      (activeFc.l80 != null && activeFc.u80 != null ? `, 80% range ${inr(activeFc.l80)} to ${inr(activeFc.u80)}` : "") +
      (activeFc.l90 != null && activeFc.u90 != null ? `, 90% range ${inr(activeFc.l90)} to ${inr(activeFc.u90)}` : "")
      : "",
  ].filter(Boolean).join(". ");

  const tipLeft = activeDay != null ? sx(activeDay) : 0;
  const tipStyle = tipLeft > width / 2 ? { right: `${Math.max(0, width - tipLeft + 10)}px` }
    : { left: `${Math.min(width - 180, tipLeft + 10)}px` };

  const longSpan = model.xMax - startDay > WEEKLY_TICKS_MAX_SPAN;

  return (
    <div className="chart" ref={wrapRef}>
      <svg viewBox={`0 0 ${width} ${HEIGHT}`} width={width} height={HEIGHT} role="img" aria-label={label}
           aria-describedby={readoutId} tabIndex={0} onKeyDown={onKey} onBlur={() => setActive(null)}
           data-testid="price-chart">
        <g className="grid">
          {model.yTicks.map((v) => <line key={v} x1={M.left} x2={M.left + plotW} y1={sy(v)} y2={sy(v)} />)}
        </g>
        <g className="axis">
          <line x1={M.left} x2={M.left + plotW} y1={M.top + plotH} y2={M.top + plotH} />
        </g>
        <g className="tick">
          {model.yTicks.map((v) => (
            <text key={v} x={M.left - 6} y={sy(v)} dy="0.32em" textAnchor="end">{axisRupees(v, yHi)}</text>
          ))}
          <text className="axis-title" x={4} y={11}>₹/quintal</text>
          {xTicks.map((d, i) => (
            <text key={d} x={sx(d)} y={M.top + plotH + 18} textAnchor="middle">
              {longSpan ? monthTickLabel(d, i) : formatShortDate(isoFromDay(d))}
            </text>
          ))}
        </g>

        {showMinMax && runs.map((run, i) => {
          const pts = run.filter((p) => p.min != null && p.max != null)
            .map((p) => ({ x: p.x, lower: p.min as number, upper: p.max as number }));
          return pts.length > 1 ? <path key={`mm${i}`} className="minmax-band" d={bandPath(pts, sx, sy)} /> : null;
        })}

        {model.fc.length > 0 && (
          <g data-testid="forecast-layer">
            {level === 0.9 && <path className="band-90" d={bandFor(0.9)} data-testid="band-90" />}
            <path className="band-80" d={bandFor(0.8)} data-testid="band-80" />
          </g>
        )}

        {model.issueDay != null && model.issueDay >= startDay && (
          <g>
            <line className="issue-line" x1={sx(model.issueDay)} x2={sx(model.issueDay)} y1={M.top - 6}
                  y2={M.top + plotH} />
            <text className="issue-label" x={sx(model.issueDay) - 4} y={M.top - 9} textAnchor="end">
              {t.chart.issued(formatShortDate(isoFromDay(model.issueDay)))}
            </text>
          </g>
        )}

        {runs.map((run, i) => run.length > 1
          ? <path key={`r${i}`} className="history-line" d={linePath(run, sx, sy)} data-testid="history-run" />
          : <circle key={`r${i}`} className="history-dot" cx={sx(run[0]!.x)} cy={sy(run[0]!.y)} r={2.5} />)}
        {showDots && model.hist.map((p) => (
          <circle key={`d${p.x}`} className="history-dot" cx={sx(p.x)} cy={sy(p.y)} r={1.6} />
        ))}

        {model.fc.length > 0 && (
          <path className="forecast-line" data-testid="forecast-line"
                d={linePath(model.start ? [model.start, ...model.fc] : model.fc, sx, sy)} />
        )}

        {fcLast && (
          <>
            {/* No text label here: the day 7 / day 14 values are printed above the chart, and a second end label
                collides with the latest-price label on narrow screens. */}
            <circle className="end-dot" cx={sx(fcLast.x)} cy={sy(fcLast.y)} r={4} fill="var(--forecast)" />
          </>
        )}
        {last && (
          <>
            <circle className="end-dot" cx={sx(last.x)} cy={sy(last.y)} r={4} fill="var(--history)" />
            <text className="end-label strong" x={sx(last.x) - 8}
                  y={sy(last.y) + (sy(last.y) - M.top < 24 ? 18 : -10)} textAnchor="end">
              {`${inr(last.y)} · ${formatShortDate(last.date)}`}
            </text>
          </>
        )}

        {activeDay != null && (
          <line className="crosshair" x1={sx(activeDay)} x2={sx(activeDay)} y1={M.top} y2={M.top + plotH} />
        )}
        <rect className="hit" x={M.left} y={M.top} width={plotW} height={plotH} onPointerMove={onPointer}
              onPointerDown={onPointer} onPointerLeave={() => setActive(null)} />
      </svg>

      {activeDay != null && (
        <div className="tooltip" style={tipStyle} aria-hidden="true" data-testid="chart-tooltip">
          <div className="date">{formatDate(isoFromDay(activeDay))}</div>
          {activeHist && (
            <div className="r"><span className="name"><span className="key" style={{ borderColor: "var(--history)" }} />
              Reported</span><strong>{inr(activeHist.y)}</strong></div>
          )}
          {activeHist && activeHist.min != null && activeHist.max != null && (
            <div className="r"><span className="name">Min–max</span>
              <span className="num">{inr(activeHist.min)}–{inr(activeHist.max)}</span></div>
          )}
          {activeFc && (
            <div className="r"><span className="name">
              <span className="key dashed" style={{ borderColor: "var(--forecast)" }} />Forecast (day {activeFc.h})
            </span><strong>{inr(activeFc.y)}</strong></div>
          )}
          {activeFc && activeFc.l80 != null && activeFc.u80 != null && (
            <div className="r"><span className="name">80% range</span>
              <span className="num">{inr(activeFc.l80)}–{inr(activeFc.u80)}</span></div>
          )}
          {activeFc && level === 0.9 && activeFc.l90 != null && activeFc.u90 != null && (
            <div className="r"><span className="name">90% range</span>
              <span className="num">{inr(activeFc.l90)}–{inr(activeFc.u90)}</span></div>
          )}
        </div>
      )}
      <p id={readoutId} className="visually-hidden" aria-live="polite">{readout}</p>
    </div>
  );
}

export function PriceLegend({ showMinMax, hasForecast, level }: {
  showMinMax: boolean; hasForecast: boolean; level: 0.8 | 0.9;
}) {
  return (
    <ul className="legend" aria-label="Legend">
      <li><span className="key" style={{ borderColor: "var(--history)" }} />{t.chart.reported}</li>
      {showMinMax && (
        <li><span className="key box" style={{ background: "var(--history)", opacity: 0.25 }} />{t.chart.minMax}</li>
      )}
      {hasForecast && <li><span className="key dashed" style={{ borderColor: "var(--forecast)" }} />{t.chart.forecast}</li>}
      {hasForecast && (
        <li><span className="key box" style={{ background: "var(--forecast)", opacity: 0.45 }} />{t.chart.range(80)}</li>
      )}
      {hasForecast && level === 0.9 && (
        <li><span className="key box" style={{ background: "var(--forecast)", opacity: 0.2 }} />{t.chart.range(90)}</li>
      )}
    </ul>
  );
}
