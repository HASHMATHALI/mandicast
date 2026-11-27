"""Backtest of the promotion gate (methodology C10b) on the real data, and the "deliberately worse model" test.

    python -I scripts/backtest_gate.py [--commodity Onion ...]

For each quarter start from 2025-10-01 to 2026-10-01: train the candidate with the frozen method (as `train-model`
would at that date) and apply C10b against the previous quarter's model (the production model it would replace)
and last value, on the candidate's early-stopping set. Then, at 2026-10-01, three deliberately worse candidates:
  * biased:   the real candidate's forecasts multiplied by 1.08;
  * shuffled: LightGBM trained on shuffled labels for 200 rounds without early stopping;
  * inverted: the real candidate's predicted change reversed (ŷ = last² / ŷ_candidate).
The gate must reject all three. Models here are trained only for this test and are not registered or served.
Data: today's database (late revisions included, as in the Phase 3 walk-forward). Writes reports/phase5/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandicast.config import get_settings  # noqa: E402
from mandicast.db.session import get_engine  # noqa: E402
from mandicast.forecast import gbm  # noqa: E402
from mandicast.forecast.data import series_info  # noqa: E402
from mandicast.serving import gate, registry  # noqa: E402

CUTOFFS = [date(2025, 7, 1), date(2025, 10, 1), date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1),
           date(2026, 10, 1)]


def shuffled_model(examples: pl.DataFrame, cutoff: date, seed: int = 7):
    from datetime import timedelta

    import lightgbm as lgb
    tr = examples.filter(pl.col("target_date") < cutoff - timedelta(days=gbm.VALID_DAYS))
    y = (tr["y"].log() - tr["log_last"]).to_numpy()
    rng = np.random.default_rng(seed)
    ds = lgb.Dataset(gbm._matrix(tr).to_pandas(), rng.permutation(y), categorical_feature=gbm.CATEGORICAL)
    return lgb.train({**gbm.PARAMS, "seed": seed}, ds, num_boost_round=200)


def summary(res: dict) -> dict:
    m = res["metrics"]
    return {"passed": res["passed"], "failed": res["failed"], "points": res["points"],
            "wape_candidate": round(m["candidate"]["wape"], 5), "wape_last_value": round(m["last_value"]["wape"], 5),
            "wape_production": None if m["production"] is None else round(m["production"]["wape"], 5)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commodity", action="append")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    eng = get_engine()
    registry.check_code_unchanged()
    out = {"run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "rule": "methodology C10b",
           "cutoffs": [str(c) for c in CUTOFFS], "commodities": {}}
    for c in a.commodity or get_settings().commodities:
        t0 = time.perf_counter()
        primary = registry.load_serving_primary(eng, c, refresh=False)
        states = series_info(eng, c).select("series_id", "state")
        examples = registry.training_examples(primary, states, CUTOFFS[-1])
        models = {}
        for cut in CUTOFFS:
            models[cut] = gbm.train(examples, cut)
            print(c, cut, "best iteration", models[cut][1]["best_iteration"], flush=True)
        rows = []
        for prev, cut in zip(CUTOFFS, CUTOFFS[1:], strict=False):
            e = gate.gate_set(examples, cut)
            res = gate.evaluate(e, gate.predict(models[cut][0], e), gate.predict(models[prev][0], e))
            rows.append({"quarter_start": str(cut), "production_trained": str(prev),
                         "best_iteration": models[cut][1]["best_iteration"], **summary(res)})
            print(c, cut, rows[-1], flush=True)
        cut, prev = CUTOFFS[-1], CUTOFFS[-2]
        e = gate.gate_set(examples, cut)
        cand = gate.predict(models[cut][0], e)
        champ = gate.predict(models[prev][0], e)
        last = e["last_price"].to_numpy()
        worse = {"biased_x1.08": cand * 1.08,
                 "shuffled_labels_200_rounds": gate.predict(shuffled_model(examples, cut), e),
                 "inverted_change": last ** 2 / cand}
        sabotage = {}
        for name, yhat in worse.items():
            sabotage[name] = summary(gate.evaluate(e, yhat, champ))
            print(c, "deliberately worse:", name, sabotage[name], flush=True)
        out["commodities"][c] = {"quarters": rows, "deliberately_worse_at_2026_10_01": sabotage,
                                 "all_deliberately_worse_rejected": all(not v["passed"] for v in sabotage.values()),
                                 "seconds": round(time.perf_counter() - t0, 1)}
    d = ROOT / "reports" / "phase5"
    d.mkdir(parents=True, exist_ok=True)
    (d / "gate_backtest.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print("written", d / "gate_backtest.json")
    return 0 if all(v["all_deliberately_worse_rejected"] for v in out["commodities"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
