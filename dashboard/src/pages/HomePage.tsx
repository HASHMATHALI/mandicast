// F1 discovery, F3 latest reported prices, F9 freshness.
import { useId } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useCommodities, useFreshness, useLatestPrices, useStates } from "../api/queries";
import { CropChips, ErrorNotice, Loading, SourceNote } from "../components/common";
import { MarketPicker } from "../components/MarketPicker";
import { formatDate, formatDateTime, inr, inrPerKg } from "../lib/format";
import { t } from "../strings";

function useCropParam(): [number | undefined, (id: number) => void, URLSearchParams] {
  const [params, setParams] = useSearchParams();
  const commodities = useCommodities();
  const raw = Number(params.get("crop"));
  const list = commodities.data ?? [];
  const crop = list.find((c) => c.id === raw)?.id ?? list[0]?.id;
  const set = (id: number) => {
    const next = new URLSearchParams(params);
    next.set("crop", String(id));
    next.delete("state");
    setParams(next, { replace: true });
  };
  return [crop, set, params];
}

export function HomePage() {
  const navigate = useNavigate();
  const stateSelectId = useId();
  const commodities = useCommodities();
  const [crop, setCrop, params] = useCropParam();
  const [, setParams] = useSearchParams();
  const stateId = Number(params.get("state")) || undefined;
  const states = useStates(crop);
  const latest = useLatestPrices(crop, stateId);
  const freshness = useFreshness();
  const cropName = commodities.data?.find((c) => c.id === crop)?.name ?? "";
  const fresh = freshness.data?.commodities.find((c) => c.commodity_id === crop);
  const firstPage = latest.data?.pages[0];

  return (
    <section>
      <h1>{t.home.title}</h1>
      <p className="muted">{t.tagline}.</p>

      {commodities.isPending && <Loading />}
      {commodities.isError && <ErrorNotice error={commodities.error} onRetry={() => commodities.refetch()} />}
      {commodities.data && (
        <div className="card stack">
          <CropChips commodities={commodities.data} selected={crop} onSelect={setCrop} />
          <div className="fields cols-2">
            <div className="field">
              <label htmlFor={stateSelectId}>{t.home.state}</label>
              <select id={stateSelectId} value={stateId ?? ""} onChange={(e) => {
                const next = new URLSearchParams(params);
                if (e.target.value) next.set("state", e.target.value); else next.delete("state");
                setParams(next, { replace: true });
              }}>
                <option value="">{t.home.allStates}</option>
                {states.data?.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.markets})</option>)}
              </select>
            </div>
            <MarketPicker commodityId={crop} stateId={stateId} label={t.home.search}
                          onPick={(m) => navigate(`/mandi/${m.id}?crop=${crop}`)} />
          </div>
        </div>
      )}

      {fresh && (
        <p className="small muted" data-testid="freshness">
          {cropName}: reports up to {fresh.latest_arrival_date ? formatDate(fresh.latest_arrival_date) : "—"};{" "}
          {fresh.reported_last_14_days} mandis reported in the last 14 days.
          {freshness.data?.last_successful_ingestion &&
            <> Updated {formatDateTime(String(freshness.data.last_successful_ingestion.finished_at))}.</>}
        </p>
      )}

      {crop !== undefined && (
        <div className="card">
          <h2>{t.home.latestTitle}{cropName && `: ${cropName}`}</h2>
          <p className="small muted">{t.home.latestNote}</p>
          {latest.isPending && <Loading />}
          {latest.isError && <ErrorNotice error={latest.error} onRetry={() => latest.refetch()} />}
          {latest.data && (
            <>
              <ul className="list" data-testid="latest-list">
                {latest.data.pages.flatMap((p) => p.items).map((it) => (
                  <li key={it.series_id}>
                    <Link className="item" to={`/mandi/${it.market.id}?crop=${crop}`}>
                      <span>
                        <span className="title">{it.market.name}</span><br />
                        <span className="meta">{it.market.district.name}, {it.market.state.name} ·{" "}
                          {t.home.reported} {formatDate(it.date)}
                          {it.days_behind_latest > 0 && ` (${it.days_behind_latest} days before the latest data)`}
                        </span>
                      </span>
                      <span className="price">
                        <strong className="num">{inr(it.modal_price)}</strong><br />
                        <span className="meta num">{inrPerKg(it.modal_price)}/kg</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
              {latest.hasNextPage && (
                <button type="button" onClick={() => latest.fetchNextPage()} disabled={latest.isFetchingNextPage}>
                  {latest.isFetchingNextPage ? t.loading : t.home.showMore}
                </button>
              )}
            </>
          )}
        </div>
      )}
      <SourceNote source={firstPage?.source} asOf={firstPage?.data_as_of} />
    </section>
  );
}
