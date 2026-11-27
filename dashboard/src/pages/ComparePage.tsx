// F4: one crop at up to 4 mandis on the dates when all of them reported.
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useCommodities, useCompare, useMarket } from "../api/queries";
import { CompareChart } from "../components/CompareChart";
import { assignSlots, SERIES_COLORS } from "../lib/forecast";
import { CropChips, ErrorNotice, Loading, Notice, Segmented, SourceNote } from "../components/common";
import { MarketPicker } from "../components/MarketPicker";
import { addDays, formatDate, inr } from "../lib/format";
import { t } from "../strings";

const MAX = 4;
const PERIODS = [{ value: 91, label: "3M" }, { value: 182, label: "6M" }, { value: 365, label: "1Y" }];

/** Colour per mandi, kept while it stays selected (derived state updated when the selection changes). */
function useStableColors(ids: number[]): (id: number) => string {
  const key = ids.join(",");
  const [state, setState] = useState(() => ({ key, slots: assignSlots(new Map(), ids) }));
  if (state.key !== key) setState({ key, slots: assignSlots(state.slots, ids) });
  return (id: number) => SERIES_COLORS[state.slots.get(id) ?? 0] ?? SERIES_COLORS[0];
}

function SelectedChip({ id, color, onRemove }: { id: number; color: string; onRemove: () => void }) {
  const m = useMarket(id);
  const name = m.data?.name ?? `#${id}`;
  return (
    <li className="row">
      <span className="key" style={{ borderColor: color }} />
      <Link to={`/mandi/${id}`}>{name}</Link>
      {m.data && <span className="meta small muted">{m.data.state.name}</span>}
      <button type="button" className="link" aria-label={t.compare.remove(name)} onClick={onRemove}>×</button>
    </li>
  );
}

export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const commodities = useCommodities();
  const [period, setPeriod] = useState(91);
  const [view, setView] = useState<"chart" | "table">("chart");
  const list = commodities.data ?? [];
  const crop = list.find((c) => c.id === Number(params.get("crop"))) ?? list[0];
  const ids = useMemo(() => [...new Set((params.get("m") ?? "").split(",").map(Number)
    .filter((n) => Number.isInteger(n) && n > 0))].slice(0, MAX), [params]);
  const colorOf = useStableColors(ids);
  const to = crop?.last_date ?? undefined;
  const from = to ? addDays(to, -period) : undefined;
  const compare = useCompare(crop?.id, ids, from, to);

  const update = (next: { crop?: number; ids?: number[] }) => {
    const p = new URLSearchParams(params);
    if (next.crop !== undefined) p.set("crop", String(next.crop));
    const nextIds = next.ids ?? ids;
    if (nextIds.length) p.set("m", nextIds.join(",")); else p.delete("m");
    setParams(p, { replace: true });
  };

  return (
    <section>
      <h1>{t.compare.title}</h1>
      <p className="muted">{t.compare.intro}</p>
      {commodities.isPending && <Loading />}
      {commodities.isError && <ErrorNotice error={commodities.error} onRetry={() => commodities.refetch()} />}
      {crop && (
        <div className="card stack">
          <CropChips commodities={list} selected={crop.id} onSelect={(id) => update({ crop: id, ids: [] })} />
          {ids.length > 0 && (
            <ul className="list" aria-label="Selected mandis">
              {ids.map((id) => (
                <SelectedChip key={id} id={id} color={colorOf(id)}
                              onRemove={() => update({ ids: ids.filter((x) => x !== id) })} />
              ))}
            </ul>
          )}
          {ids.length < MAX ? (
            <MarketPicker commodityId={crop.id} label={t.compare.add} exclude={ids}
                          onPick={(m) => update({ ids: [...ids, m.id] })} />
          ) : <p className="small muted">{t.compare.max}</p>}
        </div>
      )}

      {ids.length < 2 && <Notice>{t.compare.needTwo}</Notice>}
      {ids.length >= 2 && (
        <div className="card">
          <div className="controls">
            <Segmented label={t.chart.period} value={period} onChange={setPeriod} options={PERIODS} />
            <button type="button" onClick={() => setView(view === "chart" ? "table" : "chart")}>
              {view === "chart" ? t.chart.showTable : t.chart.showChart}
            </button>
          </div>
          {compare.isPending && <Loading />}
          {compare.isError && <ErrorNotice error={compare.error} onRetry={() => compare.refetch()} />}
          {compare.data && (
            <div className={compare.isPlaceholderData ? "refetching" : undefined}>
              <p className="small muted">{t.compare.commonDates(compare.data.common_dates)}</p>
              {compare.data.items.length === 0 ? <Notice>None of the dates in this period have reports from all the
                selected mandis. Try a longer period.</Notice> : view === "chart" ? (
                <>
                  <ul className="legend" aria-label="Legend">
                    {compare.data.markets.map((mk) => (
                      <li key={mk.market_id}>
                        <span className="key" style={{ borderColor: colorOf(mk.market_id) }} />
                        {mk.market} ({mk.state})
                      </li>
                    ))}
                  </ul>
                  <CompareChart data={compare.data} colorOf={colorOf}
                                label={`${compare.data.commodity.name} modal price at ` +
                                  compare.data.markets.map((x) => x.market).join(", ") +
                                  `, ${formatDate(compare.data.from)} to ${formatDate(compare.data.to)}. ` +
                                  "Use the arrow keys to read values; a table view is available."} />
                  <p className="chart-note">{t.chart.gapNote}</p>
                </>
              ) : (
                <div className="table-wrap">
                  <table data-testid="compare-table">
                    <caption>Modal price, ₹ per quintal</caption>
                    <thead><tr><th>Date</th>{compare.data.markets.map((mk) => (
                      <th key={mk.market_id} className="num">{mk.market}</th>))}</tr></thead>
                    <tbody>
                      {[...compare.data.items].reverse().map((r) => (
                        <tr key={r.date}><td>{formatDate(r.date)}</td>
                          {r.modal_prices.map((v, i) => <td key={i} className="num">{inr(v)}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <SourceNote source={compare.data.source} asOf={compare.data.data_as_of} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
