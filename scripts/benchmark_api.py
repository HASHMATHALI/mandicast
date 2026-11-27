"""Latency of the running API on the real database (sequential requests, one client).

    API_RATE_LIMIT_PER_MINUTE=100000 python -m mandicast api      # in another terminal
    python scripts/benchmark_api.py [--base-url http://127.0.0.1:8000] [--markets 40]

For each endpoint, requests for different mandis are cache misses (database work); repeating the same request
gives cache hits (Redis). Reports p50 / p95 / max in milliseconds, measured at the client, and writes
reports/phase4/benchmark.json. Numbers depend on the machine; they are not a capacity test.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(q * (len(xs) - 1)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--markets", type=int, default=40)
    ap.add_argument("--commodity-id", type=int, default=3)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    c = httpx.Client(base_url=a.base_url, timeout=30)
    listed = c.get(f"/v1/markets?commodity_id={a.commodity_id}&has_forecast=true&limit={a.markets}").json()
    markets = [i["id"] for i in listed["items"]]
    cid = a.commodity_id
    per_market = {
        "prices (365 days, limit 500)": lambda m: f"/v1/prices?commodity_id={cid}&market_id={m}&limit=500",
        "trends": lambda m: f"/v1/trends?commodity_id={cid}&market_id={m}",
        "forecasts (h=14)": lambda m: f"/v1/forecasts?commodity_id={cid}&market_id={m}&horizon=14",
        "eligibility": lambda m: f"/v1/forecasts/eligibility?commodity_id={cid}&market_id={m}",
        "market detail": lambda m: f"/v1/markets/{m}",
    }
    pairs = list(zip(markets[0::2], markets[1::2], strict=False))
    results = {}

    def run(name: str, urls: list[str]) -> None:
        miss, hit, codes = [], [], {}
        for u in urls:
            for bucket in (miss, hit):  # first request: miss; same request again: hit
                t = time.perf_counter()
                r = c.get(u)
                bucket.append(1000 * (time.perf_counter() - t))
                codes[r.status_code] = codes.get(r.status_code, 0) + 1
                if bucket is miss and r.headers.get("X-Cache") == "HIT":
                    miss.pop()  # already cached by an earlier run; not a miss
        results[name] = {"requests": len(urls) * 2, "status_codes": codes,
                         "miss_ms": {"n": len(miss), "p50": round(statistics.median(miss), 1) if miss else None,
                                     "p95": round(pct(miss, 0.95), 1) if miss else None,
                                     "max": round(max(miss), 1) if miss else None},
                         "hit_ms": {"n": len(hit), "p50": round(statistics.median(hit), 1),
                                    "p95": round(pct(hit, 0.95), 1), "max": round(max(hit), 1)}}

    for name, f in per_market.items():
        run(name, [f(m) for m in markets])
    run("compare (2 mandis, 90 days)", [f"/v1/compare?commodity_id={cid}&market_ids={x},{y}" for x, y in pairs])
    run("prices/latest (limit 100)", [f"/v1/prices/latest?commodity_id={k}&limit=100" for k in (1, 3, 4, 5, 6)])
    run("commodities", ["/v1/commodities"])
    run("freshness", ["/v1/freshness"])
    out = {"measured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "base_url": a.base_url,
           "machine": {"os": platform.platform(), "python": platform.python_version(),
                       "processor": platform.processor()},
           "setup": "one uvicorn worker; PostgreSQL 16 and Redis 8 in Docker Desktop on the same laptop; "
                    "sequential requests from one client",
           "markets": len(markets), "results": results}
    d = ROOT / "reports" / "phase4"
    d.mkdir(parents=True, exist_ok=True)
    (d / "benchmark.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    for k, v in results.items():
        print(f"{k:30s} miss p50 {v['miss_ms']['p50']} p95 {v['miss_ms']['p95']} (n={v['miss_ms']['n']}) | "
              f"hit p50 {v['hit_ms']['p50']} p95 {v['hit_ms']['p95']} | codes {v['status_codes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
