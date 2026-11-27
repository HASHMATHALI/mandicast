// TanStack Query hooks, one per API resource. Query keys mirror the request so cached data is reused across pages.
import { keepPreviousData, QueryClient, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useSession } from "../auth/session";
import { api, ApiError } from "./client";
import type {
  AlertOut, Commodity, CompareResponse, EligibilityResponse, ForecastResponse, FreshnessResponse, LatestResponse,
  MarketDetail, MarketPage, ModelEvaluation, ModelSummary, MonitoringResponse, NotificationOut, PriceItem,
  PricesResponse, StateItem, TrendsResponse,
} from "./types";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        // Retry only failures that may be temporary (network, timeouts, 5xx); never 4xx such as 404, 422 or 429.
        retry: (count, err) => err instanceof ApiError && (err.status === 0 || err.status >= 500) && count < 2,
      },
    },
  });
}

export const useCommodities = () =>
  useQuery({ queryKey: ["commodities"], queryFn: ({ signal }) => api.get<Commodity[]>("/commodities", {}, signal),
             staleTime: 60 * 60_000 });

export const useStates = (commodityId: number | undefined) =>
  useQuery({
    queryKey: ["states", commodityId],
    queryFn: ({ signal }) => api.get<StateItem[]>("/states", { commodity_id: commodityId }, signal),
    enabled: commodityId !== undefined, staleTime: 60 * 60_000,
  });

export const useMarketSearch = (commodityId: number | undefined, q: string, stateId?: number, hasForecast?: boolean) =>
  useQuery({
    queryKey: ["markets", commodityId, q, stateId ?? null, hasForecast ?? null],
    queryFn: ({ signal }) => api.get<MarketPage>("/markets", {
      commodity_id: commodityId, q, state_id: stateId, has_forecast: hasForecast || undefined, limit: 20,
    }, signal),
    enabled: commodityId !== undefined && q.trim().length >= 2,
    placeholderData: keepPreviousData,
  });

export const useMarket = (marketId: number) =>
  useQuery({ queryKey: ["market", marketId],
             queryFn: ({ signal }) => api.get<MarketDetail>(`/markets/${marketId}`, {}, signal),
             enabled: Number.isInteger(marketId) && marketId > 0 });

export const useLatestPrices = (commodityId: number | undefined, stateId?: number) =>
  useInfiniteQuery({
    queryKey: ["latest", commodityId, stateId ?? null],
    queryFn: ({ signal, pageParam }) => api.get<LatestResponse>("/prices/latest", {
      commodity_id: commodityId, state_id: stateId, limit: 25, cursor: pageParam,
    }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: commodityId !== undefined,
  });

/** Every daily row of a series between `from` and `to` (follows the API's pages, at most 10 × 500 rows). */
export const usePrices = (commodityId: number, marketId: number, from: string | undefined, to: string | undefined) =>
  useQuery({
    queryKey: ["prices", commodityId, marketId, from, to],
    queryFn: async ({ signal }) => {
      let cursor: string | undefined;
      let first: PricesResponse | undefined;
      const items: PriceItem[] = [];
      for (let page = 0; page < 10; page++) {
        const res = await api.get<PricesResponse>("/prices", {
          commodity_id: commodityId, market_id: marketId, from, to, limit: 500, cursor,
        }, signal);
        first ??= res;
        items.push(...res.items);
        if (!res.next_cursor) break;
        cursor = res.next_cursor;
      }
      return { ...(first as PricesResponse), items, next_cursor: null };
    },
    enabled: from !== undefined && to !== undefined,
    placeholderData: keepPreviousData,
  });

export const useTrends = (commodityId: number, marketId: number, enabled = true) =>
  useQuery({
    queryKey: ["trends", commodityId, marketId],
    queryFn: ({ signal }) => api.get<TrendsResponse>("/trends", { commodity_id: commodityId, market_id: marketId },
                                                     signal),
    enabled,
  });

export const useForecast = (commodityId: number, marketId: number, horizon: 7 | 14, enabled = true) =>
  useQuery({
    queryKey: ["forecast", commodityId, marketId, horizon],
    queryFn: ({ signal }) => api.get<ForecastResponse>("/forecasts", {
      commodity_id: commodityId, market_id: marketId, horizon,
    }, signal),
    enabled,
    placeholderData: keepPreviousData,
  });

export const useEligibility = (commodityId: number, marketId: number, enabled = true) =>
  useQuery({
    queryKey: ["eligibility", commodityId, marketId],
    queryFn: ({ signal }) => api.get<EligibilityResponse>("/forecasts/eligibility", {
      commodity_id: commodityId, market_id: marketId,
    }, signal),
    enabled,
  });

export const useCompare = (commodityId: number | undefined, marketIds: number[], from?: string, to?: string) =>
  useQuery({
    queryKey: ["compare", commodityId, marketIds.join(","), from ?? null, to ?? null],
    queryFn: ({ signal }) => api.get<CompareResponse>("/compare", {
      commodity_id: commodityId, market_ids: marketIds.join(","), from, to,
    }, signal),
    enabled: commodityId !== undefined && marketIds.length >= 2,
    placeholderData: keepPreviousData,
  });

export const useFreshness = () =>
  useQuery({ queryKey: ["freshness"], queryFn: ({ signal }) => api.get<FreshnessResponse>("/freshness", {}, signal) });

export const useModels = () =>
  useQuery({ queryKey: ["models"], queryFn: ({ signal }) => api.get<ModelSummary[]>("/models/current", {}, signal) });

export const useEvaluation = (version: string | undefined) =>
  useQuery({
    queryKey: ["evaluation", version],
    queryFn: ({ signal }) => api.get<ModelEvaluation>(`/models/${encodeURIComponent(version ?? "")}/evaluation`, {},
                                                      signal),
    enabled: !!version, staleTime: 60 * 60_000,
  });

export const useMonitoring = () =>
  useQuery({ queryKey: ["monitoring"],
             queryFn: ({ signal }) => api.get<MonitoringResponse>("/monitoring", {}, signal) });

export function useAlerts() {
  const session = useSession();
  return useQuery({
    queryKey: ["alerts", session?.token ?? null],
    queryFn: ({ signal }) => api.get<AlertOut[]>("/alerts", {}, signal, true),
    enabled: !!session, staleTime: 0,
  });
}

export function useAlertHistory() {
  const session = useSession();
  return useQuery({
    queryKey: ["alert-history", session?.token ?? null],
    queryFn: ({ signal }) => api.get<NotificationOut[]>("/alerts/history", {}, signal, true),
    enabled: !!session, staleTime: 0,
  });
}
