"""Phase 5: promotion gate (a deliberately worse model is not promoted), validation before training, rollback,
fallback decisions, drift, last-value-only publication, live monitoring, and their API views.

Frames, artifacts and forecast values built here are synthetic TEST inputs that exercise the rules; they are not
data or model results. Prices come from the real fixture rows loaded through the pipeline.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest
from conftest import FakeClient, db_url_for_tests, real_rows
from fastapi.testclient import TestClient
from sqlalchemy import text

import mandicast.jobs as jobs
from mandicast import pipeline
from mandicast.api.app import create_app, read_only_engine
from mandicast.config import Settings
from mandicast.db.session import session_scope
from mandicast.serving import gate, lifecycle, monitor, publish, registry

# --- gate (pure) ------------------------------------------------------------------------------------------------


def _gate_frame(n: int = 2000, seed: int = 0) -> tuple[pl.DataFrame, np.ndarray]:
    """A synthetic gate set where a 'good' forecast knows half of the true change."""
    rng = np.random.default_rng(seed)
    last = rng.uniform(1000, 3000, n)
    change = rng.normal(0, 0.08, n)
    y = last * np.exp(change)
    good = last * np.exp(0.5 * change)
    e = pl.DataFrame({"y": y, "last_price": last, "h": rng.integers(1, 15, n),
                      "origin": [date(2026, 9, 1)] * n})
    return e, good


def test_gate_promotes_a_good_candidate():
    e, good = _gate_frame()
    res = gate.evaluate(e, good, champion_yhat=e["last_price"].to_numpy() * 1.01)
    assert res["passed"] and res["failed"] == []
    assert res["metrics"]["candidate"]["wape"] < res["metrics"]["last_value"]["wape"]


@pytest.mark.parametrize("make_worse", [
    lambda e, good: good * 1.08,                                   # biased
    lambda e, good: e["last_price"].to_numpy() ** 2 / good,        # predicted change reversed
    lambda e, good: good * np.exp(np.random.default_rng(1).normal(0, 0.3, len(good))),  # noise
])
def test_gate_rejects_deliberately_worse_candidates(make_worse):
    e, good = _gate_frame()
    res = gate.evaluate(e, make_worse(e, good), champion_yhat=good)
    assert not res["passed"]
    assert any(r.startswith("(ii)") or r.startswith("(iii)") for r in res["failed"])


def test_gate_rejects_broken_predictions_and_tolerates_slightly_worse():
    e, good = _gate_frame()
    broken = good.copy()
    broken[0] = np.inf
    assert gate.evaluate(e, broken, None)["failed"][0].startswith("(i)")
    exploding = good.copy()
    exploding[1] = e["last_price"][1] * 20  # |log ratio| ≈ 3 > 2
    assert gate.evaluate(e, exploding, None)["failed"][0].startswith("(i)")
    slightly = good * 1.0005  # within the 2% tolerance against the production model
    assert gate.evaluate(e, slightly, good)["passed"]


def test_production_comparison_blocks_only_if_the_production_model_could_still_serve():
    e, good = _gate_frame()
    better_champion = good * 0.0 + e["y"].to_numpy()  # a perfect production model: (iii) fails
    blocking = gate.evaluate(e, good, better_champion, champion_can_serve=True)
    assert not blocking["passed"] and blocking["failed"][0].startswith("(iii)")
    stale = gate.evaluate(e, good, better_champion, champion_can_serve=False)
    assert stale["passed"] and stale["not_blocking"] and stale["rules"][-1]["outcome"] == "failed"
    worse = gate.evaluate(e, good * 1.08, better_champion, champion_can_serve=False)
    assert not worse["passed"]  # still rejected by (ii): not better than last value


def test_gate_skips_the_production_comparison_when_state_codes_differ():
    e, good = _gate_frame()
    res = gate.evaluate(e, good, champion_yhat=good * 0.0 + e["y"].to_numpy(), champion_comparable=False)
    assert res["passed"] and "skipped" in res["rules"][-1]["rule"]


def test_validation_before_training():
    e, _ = _gate_frame(50)
    ex = e.with_columns(pl.col("last_price").log().alias("log_last"))
    cutoff = date(2026, 10, 1)
    ok = gate.validate_data(ex, ex.head(0).vstack(ex).head(1500) if ex.height >= 1500 else ex, cutoff,
                            date(2026, 9, 28), None)
    names = {c["name"]: c["ok"] for c in ok["checks"]}
    assert names["data_reaches_cutoff"] and not names["gate_set_size"]  # 50 examples < 1,000
    stale = gate.validate_data(ex, ex, cutoff, date(2026, 9, 1), {"examples": 10})
    assert {c["name"]: c["ok"] for c in stale["checks"]}["data_reaches_cutoff"] is False
    shrunk = gate.validate_data(ex, ex, cutoff, date(2026, 9, 30), {"examples": 1000})
    assert {c["name"]: c["ok"] for c in shrunk["checks"]}["examples_vs_production"] is False


# --- drift and fallback (pure) ----------------------------------------------------------------------------------

def test_psi_is_small_for_the_same_distribution_and_large_for_a_shift():
    rng = np.random.default_rng(3)
    ref_vals = rng.normal(0, 1, 5000)
    edges = sorted({float(x) for x in np.quantile(ref_vals, np.linspace(0.1, 0.9, 9))})
    counts = np.bincount(np.searchsorted(edges, ref_vals, side="right"), minlength=len(edges) + 1)
    ref = {"edges": edges, "shares": list(counts / len(ref_vals))}
    assert publish.psi(ref, rng.normal(0, 1, 2000)) < publish.PSI_NOTICE
    assert publish.psi(ref, rng.normal(1.0, 1, 2000)) > publish.PSI_WARNING


def _pool(origin: date, model_error: float, lv_error: float, weeks: int = 8, series: int = 20) -> tuple:
    rows, prices = [], []
    for w in range(1, weeks + 1):
        o = origin - timedelta(weeks=w)
        for s in range(series):
            for h in range(1, 15):
                y = 1000.0 + s
                rows.append((s, o, h, y * (1 + model_error), y * (1 + lv_error)))
                prices.append((s, o + timedelta(days=h), y, True))
    pool = pl.DataFrame(rows, schema=publish.POOL_SCHEMA, orient="row")
    primary = pl.DataFrame(prices, schema={"series_id": pl.Int32, "date": pl.Date, "price": pl.Float64,
                                           "input_ok": pl.Boolean}, orient="row").unique(["series_id", "date"])
    return pool, primary


def test_fallback_comparison_is_informational_under_the_amended_rule(monkeypatch):
    o = date(2026, 11, 23)
    pool, primary = _pool(o, model_error=0.10, lv_error=0.05)
    d = publish.fallback_decisions(primary, pool, o)
    assert d["1-7"]["model_lost_recently"] and d["1-7"]["points"] >= publish.FALLBACK_MIN_POINTS
    assert d["1-7"]["served_by"] == "model" and "not applied" in d["1-7"]["reason"]  # AUTO_FALLBACK off
    monkeypatch.setattr(publish, "AUTO_FALLBACK", True)  # the rule as first recorded
    assert publish.fallback_decisions(primary, pool, o)["1-7"]["served_by"] == "last_value"
    pool, primary = _pool(o, model_error=0.03, lv_error=0.05)
    d = publish.fallback_decisions(primary, pool, o)
    assert d["8-14"]["served_by"] == "model" and not d["8-14"]["model_lost_recently"]
    pool, primary = _pool(o, model_error=0.10, lv_error=0.05, weeks=1, series=3)  # too few points
    d = publish.fallback_decisions(primary, pool, o)
    assert d["1-7"]["served_by"] == "model" and "too few" in d["1-7"]["reason"]


def test_fallback_ignores_points_whose_target_is_on_or_after_the_origin():
    o = date(2026, 11, 23)
    pool, primary = _pool(o, model_error=0.10, lv_error=0.05)
    late = pool.with_columns(pl.col("origin") + timedelta(weeks=8))  # origins in [o, o + 7 weeks]: not usable
    assert publish.fallback_decisions(primary, late, o)["1-7"]["points"] == 0


# --- database: lifecycle ----------------------------------------------------------------------------------------

@pytest.fixture
def loaded(db, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "REPORT_DIR", tmp_path / "reports")
    s = Settings(database_url=db_url_for_tests(), raw_dir=tmp_path / "raw", commodities=["Soyabean"],
                 request_pause_s=0, manual_aliases_path=tmp_path / "none.csv", page_size=30)
    pipeline.backfill(db, s, ["Soyabean"], client=FakeClient(real_rows()))
    with db.begin() as c:
        cid = c.execute(text("SELECT id FROM commodities WHERE name = 'Soyabean'")).scalar_one()
        markets = dict(c.execute(text("SELECT name, id FROM markets")).all())
        series = dict(c.execute(text("SELECT market_id, id FROM price_series")).all())
    return {"commodity_id": cid, "series_id": series[markets["sehore"]], "market_id": markets["sehore"],
            "tmp": tmp_path}


def _trained(tmp, cutoff: date, tag: str, examples: int = 2000) -> registry.Trained:
    path = tmp / f"model_{tag}.txt"
    path.write_text(f"test artifact {tag}", encoding="utf-8")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    info = {"forecast_code_sha256": "0" * 64, "best_iteration": 5, "train_rows": 1, "valid_rows": 1,
            "examples": examples, "seconds": 0.1, "state_codes_sha256": "s", "gate_examples": 2000}
    e, _ = _gate_frame()
    ex = e.with_columns(pl.col("last_price").log().alias("log_last"))
    return registry.Trained("Soyabean", cutoff, path, sha, info, ex, ex)


def _status(engine) -> dict:
    with engine.connect() as c:
        return dict(c.execute(text("SELECT version, status FROM model_versions")).all())


def _events(engine) -> list:
    with engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT e.event, mv.version FROM model_events e LEFT JOIN model_versions mv ON mv.id = e.model_version_id "
            "ORDER BY e.id")).all()]


@pytest.mark.db
def test_failed_gate_rejects_candidate_and_keeps_production(loaded, db, monkeypatch):
    q3 = _trained(loaded["tmp"], date(2025, 7, 1), "q3")
    with session_scope(db) as s:
        registry.register(s, q3)  # the current production model
    q4 = _trained(loaded["tmp"], date(2025, 10, 1), "q4")
    monkeypatch.setattr(lifecycle, "run_gate", lambda t, champion: {
        "passed": False, "failed": ["(iii) WAPE(candidate) ≤ 1.02 × WAPE(production)"], "metrics": {}, "rules": []})
    monkeypatch.setattr(lifecycle, "latest_complete_date", lambda s, c, today: date(2025, 9, 30))
    res = lifecycle.retrain(db, "Soyabean", date(2025, 10, 1), trained=q4)
    assert res["status"] == "rejected"
    st = _status(db)
    assert st[registry.version_name(q3)] == "production" and st[registry.version_name(q4)] == "rejected"
    assert ("rejected", registry.version_name(q4)) in _events(db)


@pytest.mark.db
def test_passed_gate_promotes_then_rollback_restores(loaded, db, monkeypatch):
    q3 = _trained(loaded["tmp"], date(2025, 7, 1), "q3")
    with session_scope(db) as s:
        registry.register(s, q3)
    q4 = _trained(loaded["tmp"], date(2025, 10, 1), "q4")
    seen = {}

    def fake_gate(t, champion):
        seen["champion"] = champion["version"]
        return {"passed": True, "failed": [], "metrics": {"candidate": {"wape": 0.1}}, "rules": []}

    monkeypatch.setattr(lifecycle, "run_gate", fake_gate)
    monkeypatch.setattr(lifecycle, "latest_complete_date", lambda s, c, today: date(2025, 9, 30))
    assert lifecycle.retrain(db, "Soyabean", date(2025, 10, 1), trained=q4)["status"] == "promoted"
    assert seen["champion"] == registry.version_name(q3)  # compared with the model it replaces
    v3, v4 = registry.version_name(q3), registry.version_name(q4)
    assert _status(db) == {v3: "archived", v4: "production"}
    with session_scope(db) as s:
        out = registry.rollback(s, "Soyabean", "test rollback")
    assert out == {"rolled_back": v4, "restored": v3}
    assert _status(db) == {v3: "production", v4: "archived"}
    ev = _events(db)
    assert ("rolled_back", v4) in ev and ev[-1] == ("promoted", v3)
    with session_scope(db) as s:  # rolling back the restored model leaves no model: last value is served
        assert registry.rollback(s, "Soyabean", "again")["restored"] is None
    assert "production" not in _status(db).values() and _events(db)[-1] == ("fallback", None)


@pytest.mark.db
def test_validation_failure_registers_nothing(loaded, db, monkeypatch):
    q4 = _trained(loaded["tmp"], date(2025, 10, 1), "q4")
    q4.gate_examples = q4.gate_examples.head(10)  # < 1,000 early-stopping examples
    monkeypatch.setattr(lifecycle, "latest_complete_date", lambda s, c, today: date(2025, 9, 30))
    res = lifecycle.retrain(db, "Soyabean", date(2025, 10, 1), trained=q4)
    assert res["status"] == "validation_failed" and _status(db) == {}
    assert _events(db) == [("rejected", None)]


@pytest.mark.db
def test_production_model_cannot_be_rejected_directly(loaded, db):
    q3 = _trained(loaded["tmp"], date(2025, 7, 1), "q3")
    with session_scope(db) as s:
        mv = registry.register(s, q3)
    with pytest.raises(RuntimeError, match="rollback"), session_scope(db) as s:
        registry.reject(s, mv["id"], "no")


# --- database: last-value publication, monitoring and the API ----------------------------------------------------

ORIGIN = date(2025, 10, 27)  # a Monday inside the fixture's range, with reported prices after it


def _publish_last_value_only(db, loaded) -> dict:
    with db.connect() as c:
        last_date, last_price = c.execute(text(
            "SELECT date, modal_price FROM price_observations WHERE series_id = :s AND is_primary AND date <= :o "
            "ORDER BY date DESC LIMIT 1"), {"s": loaded["series_id"], "o": ORIGIN}).one()
    lp = float(last_price)
    rows = pl.DataFrame({"series_id": [loaded["series_id"]] * 14, "h": list(range(1, 15)),
                         "last_date": [last_date] * 14, "last_price": [lp] * 14, "yhat": [lp] * 14,
                         "lo80": [lp * 0.9] * 14, "hi80": [lp * 1.1] * 14, "lo90": [lp * 0.85] * 14,
                         "hi90": [lp * 1.15] * 14, "volatility_tercile": [1] * 14,
                         "model_yhat": [None] * 14, "served_by": ["last_value"] * 14}).with_columns(
        pl.col("series_id").cast(pl.Int32), pl.col("h").cast(pl.Int16), pl.col("volatility_tercile").cast(pl.Int16),
        pl.col("model_yhat").cast(pl.Float64))
    decisions = {r: {"served_by": "last_value", "reason": "no production model for Soyabean"} for r in ("1-7", "8-14")}
    prep = publish.Prepared("Soyabean", ORIGIN, ORIGIN + timedelta(days=2), None, 1, rows, {"test": True}, decisions,
                            {"status": "no model"})
    with session_scope(db) as s:
        return publish.write(s, prep, job_id=None)


@pytest.mark.db
def test_last_value_publication_monitoring_and_api(loaded, db, tmp_path):
    res = _publish_last_value_only(db, loaded)
    assert res["status"] == "published" and res["model_version"] is None
    with db.connect() as c:
        run = c.execute(text("SELECT model_version_id, decisions FROM forecast_runs")).one()
        served = c.execute(text("SELECT DISTINCT served_by FROM forecasts")).scalars().all()
        nulls = c.execute(text("SELECT count(*) FROM forecasts WHERE model_point IS NULL")).scalar()
    assert run.model_version_id is None and served == ["last_value"] and nulls == 14

    with session_scope(db) as s:
        metrics = monitor.live_metrics(s, loaded["commodity_id"], date(2025, 11, 20))
        monitor.store(s, loaded["commodity_id"], date(2025, 11, 20), metrics)
        actual = dict(s.execute(text(
            "SELECT date, modal_price FROM price_observations WHERE series_id = :s AND is_primary AND date > :o "
            "AND date <= :t AND NOT ('local_outlier' = ANY(flags))"),
            {"s": loaded["series_id"], "o": ORIGIN, "t": ORIGIN + timedelta(days=7)}).all())
        last_price = float(s.execute(text("SELECT last_price FROM forecasts LIMIT 1")).scalar())
    m17 = next(m for m in metrics if m["day_range"] == "1-7")
    y = np.array([float(v) for v in actual.values()])
    assert m17["points"] == len(y)
    assert m17["wape_last_value"] == pytest.approx(np.abs(y - last_price).sum() / y.sum())
    assert m17["wape_served"] == pytest.approx(m17["wape_last_value"]) and m17["wape_model"] is None

    s_ = Settings(database_url=db_url_for_tests(), redis_url=None, api_rate_limit_per_minute=10_000)
    with TestClient(create_app(settings=s_, engine=read_only_engine(s_), use_redis=False)) as client:
        f = client.get(f"/v1/forecasts?commodity_id={loaded['commodity_id']}&market_id={loaded['market_id']}").json()
        assert f["model"] is None and f["served_by"] == {"1-7": "last_value"}
        assert {p["served_by"] for p in f["points"]} == {"last_value"}
        assert any("last reported price" in w for w in f["warnings"])
        assert f["live_accuracy"]["points"] == len(y) and "not yet reliable" in f["live_accuracy"]["note"]
        mon = client.get("/v1/monitoring").json()
        soy = mon["commodities"][0]
        assert soy["latest_publication"]["origin"] == str(ORIGIN) and len(soy["live"]) == 2
        hist = client.get(f"/v1/models/history?commodity_id={loaded['commodity_id']}").json()
        assert hist["versions"] == [] and hist["commodity"]["name"] == "Soyabean"


@pytest.mark.db
def test_operational_checks_flag_stale_data_and_missing_model(loaded, db):
    with session_scope(db) as s:
        checks = monitor.operational_checks(s, ["Soyabean"], date(2026, 11, 27))
    by = {(c["check"], c["commodity"]): c["ok"] for c in checks}
    assert by[("ingestion_recent", None)] is True  # the fixture backfill just ran
    assert by[("data_current", "Soyabean")] is False  # fixture prices end in 2025


@pytest.mark.db
def test_production_model_failing_reevaluation_is_rolled_back_and_rejected(loaded, db, monkeypatch):
    q4 = _trained(loaded["tmp"], date(2025, 10, 1), "q4")
    with session_scope(db) as s:
        registry.register(s, q4)  # promoted without the gate (as in Phase 4)
    monkeypatch.setattr(lifecycle, "run_gate", lambda t, champion: {
        "passed": False, "failed": ["(ii) WAPE(candidate) ≤ WAPE(last value)"], "metrics": {}, "rules": []})
    monkeypatch.setattr(lifecycle, "latest_complete_date", lambda s, c, today: date(2025, 9, 30))
    res = lifecycle.retrain(db, "Soyabean", date(2025, 10, 1), trained=q4)  # same artifact: re-evaluation
    assert res["status"] == "rolled_back_and_rejected"
    v = registry.version_name(q4)
    assert _status(db) == {v: "rejected"}
    assert _events(db)[-2:] == [("fallback", None), ("rejected", v)]


@pytest.mark.db
def test_training_crash_in_the_scheduled_job_keeps_production_and_records_the_failure(loaded, db, monkeypatch):
    """Failure mode: retraining crashes (here: out of memory while loading the training data, its first step) in the
    scheduled `ensure_models` job. Nothing is registered, the existing model versions are unchanged, and the job is
    recorded as failed with the reason."""
    q3 = _trained(loaded["tmp"], date(2025, 7, 1), "q3")
    with session_scope(db) as s:
        registry.register(s, q3)  # production for 2025Q3 only, so a 2025Q4 origin needs a new model

    def crash(*args, **kwargs):
        raise MemoryError("simulated: out of memory while loading training data")

    monkeypatch.setattr(registry, "load_serving_primary", crash)
    monkeypatch.setattr(registry, "train", crash)
    with pytest.raises(MemoryError):
        lifecycle.ensure_models(db, ["Soyabean"], today=date(2025, 11, 21))
    assert _status(db) == {registry.version_name(q3): "production"}
    with db.connect() as c:
        job = c.execute(text("SELECT kind, status, error FROM ingestion_jobs ORDER BY id DESC LIMIT 1")).one()
    assert job.kind == "ensure_models" and job.status == "failed" and "out of memory" in job.error
