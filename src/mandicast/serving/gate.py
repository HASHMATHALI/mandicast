"""Validation before training (methodology C10a) and the promotion gate (C10b).

Both are pure functions of data frames and models, so they can be tested with deliberately broken inputs: a model
that is worse than the current one, or than repeating the last price, must not be promoted.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import polars as pl

from mandicast.forecast import gbm

TOLERANCE = 1.02          # C10b (iii): candidate WAPE ≤ 1.02 × production WAPE
MIN_EXAMPLE_SHARE = 0.8   # C10a: ≥ 80% of the production model's training examples
MIN_GATE_EXAMPLES = 1000  # C10a: early-stopping set size
MAX_ABS_LOG_RATIO = 2.0   # C10b (i)
MAX_DATA_LAG_DAYS = 7     # C10a: latest complete date ≥ cutoff − 7 days


def gate_set(examples: pl.DataFrame, cutoff: date) -> pl.DataFrame:
    """The early-stopping examples of `gbm.train` (same filter): origin in the 105 days before the cutoff and
    target before the cutoff."""
    return examples.filter((pl.col("origin") > cutoff - timedelta(days=gbm.VALID_DAYS))
                           & (pl.col("target_date") < cutoff))


def wape(y: np.ndarray, yhat: np.ndarray) -> float:
    return float(np.abs(y - yhat).sum() / np.abs(y).sum())


def validate_data(examples: pl.DataFrame, gate_examples: pl.DataFrame, cutoff: date, latest_complete: date | None,
                  champion_training: dict | None) -> dict:
    """C10a checks. Returns {"passed": bool, "checks": [...]} (each check: name, ok, value, rule)."""
    checks = []

    def add(name: str, ok: bool, value, rule: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "value": value, "rule": rule})

    add("data_reaches_cutoff", latest_complete is not None and latest_complete >= cutoff - timedelta(
        days=MAX_DATA_LAG_DAYS), str(latest_complete), f"latest complete date ≥ cutoff − {MAX_DATA_LAG_DAYS} days")
    if champion_training and champion_training.get("examples"):
        ref = int(champion_training["examples"])
        add("examples_vs_production", examples.height >= MIN_EXAMPLE_SHARE * ref, {"candidate": examples.height,
            "production": ref}, f"≥ {MIN_EXAMPLE_SHARE:.0%} of the production model's examples")
    add("gate_set_size", gate_examples.height >= MIN_GATE_EXAMPLES, gate_examples.height,
        f"≥ {MIN_GATE_EXAMPLES} early-stopping examples")
    labels = (examples["y"].log() - examples["log_last"]).to_numpy()
    add("labels_finite", bool(np.isfinite(labels).all()), int((~np.isfinite(labels)).sum()), "no infinite or NaN label")
    return {"passed": all(c["ok"] for c in checks), "checks": checks}


def _scores(e: pl.DataFrame, yhat: np.ndarray) -> dict:
    y = e["y"].to_numpy()
    days = e["h"].to_numpy() <= 7
    out = {"wape": wape(y, yhat)}
    for name, mask in (("wape_days_1_7", days), ("wape_days_8_14", ~days)):
        out[name] = wape(y[mask], yhat[mask]) if mask.any() else None
    return out


def evaluate(e: pl.DataFrame, candidate_yhat: np.ndarray, champion_yhat: np.ndarray | None,
             champion_comparable: bool = True, champion_can_serve: bool = True) -> dict:
    """C10b on the gate set `e` (columns y, last_price, h). Predictions are aligned with the rows of `e`.

    (iii) blocks only if the production model can still serve the candidate's quarter (`champion_can_serve`);
    otherwise its outcome is recorded but not blocking, because a rejection would serve last value (amendment of
    2026-11-27 08:01, docs/06 §9b)."""
    last = e["last_price"].to_numpy()
    ratio = np.log(candidate_yhat / last) if np.all(candidate_yhat > 0) else np.array([np.inf])
    finite_positive = bool(np.isfinite(candidate_yhat).all() and (candidate_yhat > 0).all())
    max_abs = float(np.nanmax(np.abs(ratio))) if finite_positive else math.inf
    cand, lv = _scores(e, candidate_yhat), _scores(e, last)
    rules = [{"rule": "(i) predictions finite and positive, |log(ŷ/last price)| ≤ 2",
              "ok": finite_positive and max_abs <= MAX_ABS_LOG_RATIO, "value": max_abs},
             {"rule": "(ii) WAPE(candidate) ≤ WAPE(last value)", "ok": cand["wape"] <= lv["wape"],
              "value": {"candidate": cand["wape"], "last_value": lv["wape"]}}]
    champ = None
    if champion_yhat is not None and champion_comparable:
        champ = _scores(e, champion_yhat)
        ok3 = cand["wape"] <= TOLERANCE * champ["wape"]
        rule = {"rule": f"(iii) WAPE(candidate) ≤ {TOLERANCE} × WAPE(production)", "ok": ok3,
                "value": {"candidate": cand["wape"], "production": champ["wape"]}}
        if not ok3 and not champion_can_serve:
            rule.update(ok=True, not_blocking=True, outcome="failed",
                        note="the production model cannot serve the candidate's quarter (C9b); a rejection would "
                             "serve last value, so (i) and (ii) decide")
        rules.append(rule)
    elif champion_yhat is not None:
        rules.append({"rule": "(iii) skipped: production model's state codes differ (C10a)", "ok": True, "value": None})
    passed = all(r["ok"] for r in rules)
    return {"passed": passed, "rules": rules, "points": e.height,
            "origins": [str(e["origin"].min()), str(e["origin"].max())] if e.height else None,
            "metrics": {"candidate": cand, "last_value": lv, "production": champ},
            "failed": [r["rule"] for r in rules if not r["ok"]],
            "not_blocking": [r["rule"] for r in rules if r.get("not_blocking")],
            "note": "E chose the candidate's number of trees, so its score here is optimistic (C10b)"}


def predict(model, e: pl.DataFrame) -> np.ndarray:
    return gbm.predict(model, e)["yhat"].to_numpy()
