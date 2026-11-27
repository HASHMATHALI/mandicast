// One crop at up to 4 mandis on their common report dates (/v1/compare). Colours follow the mandi's position in the
// selection (slots 1–4, validated as a set); a legend and direct end labels always name the lines, and the page
// offers the same values as a table.
import { useId, useMemo, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { CompareResponse } from "../api/types";
import {
  axisRupees, dateTicks, dayNumber, isoFromDay, linearScale, linePath, monthTickLabel, nearestIndex, niceTicks,
  splitRuns, WEEKLY_TICKS_MAX_SPAN,
} from "../lib/chart";
import { formatDate, formatShortDate, inr } from "../lib/format";
import { MAX_GAP_DAYS } from "../lib/forecast";
import { useElementWidth } from "../lib/hooks";

const HEIGHT = 260;
const M = { top: 16, right: 14, bottom: 28, left: 46 };

export function CompareChart({ data, colorOf, label }: {
  data: CompareResponse; colorOf: (marketId: number) => string; label: string;
}) {
  const [wrapRef, width] = useElementWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const readoutId = useId();

  const m = useMemo(() => {
    const days = data.items.map((r) => dayNumber(r.date));
    const all = data.items.flatMap((r) => r.modal_prices);
    const yMin = all.length ? Math.min(...all) : 0;
    const yMax = all.length ? Math.max(...all) : 1;
    const pad = (yMax - yMin) * 0.06 || yMax * 0.05 || 1;
    return { days, yTicks: niceTicks(Math.max(0, yMin - pad), yMax + pad, 5) };
  }, [data]);

  const startDay = dayNumber(data.from);
  const endDay = Math.max(dayNumber(data.to), startDay + 7);
  const plotW = Math.max(120, width - M.left - M.right);
  const plotH = HEIGHT - M.top - M.bottom;
  const sx = linearScale([startDay, endDay + 0.5], [M.left, M.left + plotW]);
  const yLo = m.yTicks[0] ?? 0;
  const yHi = m.yTicks[m.yTicks.length - 1] ?? 1;
  const sy = linearScale([yLo, yHi], [M.top + plotH, M.top]);
  const xTicks = dateTicks(startDay, endDay, Math.max(2, Math.floor(plotW / 72)));
  const longSpan = endDay - startDay > WEEKLY_TICKS_MAX_SPAN;
  const lastRow = data.items[data.items.length - 1];

  // End labels: one per line, placed at its last value; if two would overlap, the legend carries identity instead.
  const ends = data.markets.map((mk, i) => ({ mk, y: lastRow ? sy(lastRow.modal_prices[i] ?? 0) : 0 }))
    .sort((a, b) => a.y - b.y);
  const endsCollide = ends.some((e, i) => i > 0 && e.y - (ends[i - 1]?.y ?? 0) < 13);

  function onPointer(e: PointerEvent<SVGRectElement>) {
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    setActive(nearestIndex(m.days, sx.invert(((e.clientX - rect.left) / rect.width) * width)));
  }
  function onKey(e: KeyboardEvent<SVGSVGElement>) {
    if (!m.days.length) return;
    const cur = active ?? m.days.length - 1;
    const keys: Record<string, number | null> = {
      ArrowLeft: Math.max(0, cur - 1), ArrowRight: Math.min(m.days.length - 1, cur + 1), Home: 0,
      End: m.days.length - 1, Escape: null,
    };
    if (!(e.key in keys)) return;
    e.preventDefault();
    setActive(keys[e.key] ?? null);
  }

  const row = active != null ? data.items[active] : undefined;
  const ax = active != null ? sx(m.days[active] ?? 0) : 0;
  const tipStyle = ax > width / 2 ? { right: `${Math.max(0, width - ax + 10)}px` }
    : { left: `${Math.min(width - 190, ax + 10)}px` };
  const readout = row ? `${formatDate(row.date)}. ` + data.markets
    .map((mk, i) => `${mk.market}: ${inr(row.modal_prices[i] ?? 0)}`).join(". ") : "";

  return (
    <div className="chart" ref={wrapRef}>
      <svg viewBox={`0 0 ${width} ${HEIGHT}`} width={width} height={HEIGHT} role="img" aria-label={label}
           aria-describedby={readoutId} tabIndex={0} onKeyDown={onKey} onBlur={() => setActive(null)}
           data-testid="compare-chart">
        <g className="grid">
          {m.yTicks.map((v) => <line key={v} x1={M.left} x2={M.left + plotW} y1={sy(v)} y2={sy(v)} />)}
        </g>
        <g className="axis"><line x1={M.left} x2={M.left + plotW} y1={M.top + plotH} y2={M.top + plotH} /></g>
        <g className="tick">
          {m.yTicks.map((v) => (
            <text key={v} x={M.left - 6} y={sy(v)} dy="0.32em" textAnchor="end">{axisRupees(v, yHi)}</text>
          ))}
          {xTicks.map((d, i) => (
            <text key={d} x={sx(d)} y={M.top + plotH + 18} textAnchor="middle">
              {longSpan ? monthTickLabel(d, i) : formatShortDate(isoFromDay(d))}
            </text>
          ))}
        </g>
        {data.markets.map((mk, i) => {
          const pts = data.items.map((r) => ({ x: dayNumber(r.date), y: r.modal_prices[i] ?? 0 }));
          return splitRuns(pts, MAX_GAP_DAYS).map((run, j) => run.length > 1
            ? <path key={`${mk.market_id}-${j}`} className="series-line" d={linePath(run, sx, sy)}
                    stroke={colorOf(mk.market_id)} />
            : <circle key={`${mk.market_id}-${j}`} cx={sx(run[0]!.x)} cy={sy(run[0]!.y)} r={2.5}
                      fill={colorOf(mk.market_id)} />);
        })}
        {lastRow && data.markets.map((mk, i) => (
          <circle key={`e${mk.market_id}`} className="end-dot" cx={sx(dayNumber(lastRow.date))}
                  cy={sy(lastRow.modal_prices[i] ?? 0)} r={4} fill={colorOf(mk.market_id)} />
        ))}
        {lastRow && !endsCollide && ends.map(({ mk, y }) => (
          <text key={`l${mk.market_id}`} className="end-label" x={sx(dayNumber(lastRow.date)) - 8} y={y - 7}
                textAnchor="end">{mk.market}</text>
        ))}
        {active != null && <line className="crosshair" x1={ax} x2={ax} y1={M.top} y2={M.top + plotH} />}
        <rect className="hit" x={M.left} y={M.top} width={plotW} height={plotH} onPointerMove={onPointer}
              onPointerDown={onPointer} onPointerLeave={() => setActive(null)} />
      </svg>
      {row && (
        <div className="tooltip" style={tipStyle} aria-hidden="true">
          <div className="date">{formatDate(row.date)}</div>
          {data.markets.map((mk, i) => (
            <div className="r" key={mk.market_id}>
              <span className="name"><span className="key" style={{ borderColor: colorOf(mk.market_id) }} />
                {mk.market}</span>
              <strong>{inr(row.modal_prices[i] ?? 0)}</strong>
            </div>
          ))}
        </div>
      )}
      <p id={readoutId} className="visually-hidden" aria-live="polite">{readout}</p>
    </div>
  );
}
