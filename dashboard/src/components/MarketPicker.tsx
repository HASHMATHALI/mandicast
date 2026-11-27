// Search box for mandis of one crop (/v1/markets?q=). Results are plain buttons, so it works with touch, mouse,
// keyboard and screen readers without a custom combobox.
import { useId, useState } from "react";
import { useMarketSearch } from "../api/queries";
import type { MarketItem } from "../api/types";
import { t } from "../strings";
import { useDebounced } from "../lib/hooks";
import { ErrorNotice } from "./common";

export function MarketPicker({ commodityId, stateId, label, onPick, exclude = [], hasForecast }: {
  commodityId: number | undefined; stateId?: number; label: string; onPick: (m: MarketItem) => void;
  exclude?: number[]; hasForecast?: boolean;
}) {
  const id = useId();
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 300);
  const search = useMarketSearch(commodityId, dq, stateId, hasForecast);
  const items = (search.data?.items ?? []).filter((m) => !exclude.includes(m.id));

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} type="search" value={q} maxLength={100} autoComplete="off" placeholder={t.home.searchPlaceholder}
             onChange={(e) => setQ(e.target.value)} aria-describedby={`${id}-hint`} />
      <small id={`${id}-hint`} className="muted">{t.home.searchHint}</small>
      {search.isError && <ErrorNotice error={search.error} onRetry={() => search.refetch()} />}
      {dq.length >= 2 && search.isSuccess && (
        items.length === 0 ? <p className="muted" role="status">{t.home.noMatches}</p> : (
          <ul className="list" aria-label={`${label}: results`}>
            {items.map((m) => (
              <li key={m.id}>
                <button type="button" className="link" onClick={() => { onPick(m); setQ(""); }}>
                  {m.name}
                </button>{" "}
                <span className="meta">{m.district.name}, {m.state.name}</span>
              </li>
            ))}
          </ul>
        )
      )}
    </div>
  );
}
