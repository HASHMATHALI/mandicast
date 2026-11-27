"""Serving rules: quarters and origins, the post-freeze code guard, calibration without look-ahead, the calibration
window extension, artifact integrity, model validity and idempotent publication.

Frames built here are small synthetic TEST inputs for the rules; they are not data or results.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest
from conftest import FakeClient, db_url_for_tests, real_rows
from sqlalchemy import text

import mandicast.jobs as jobs
from mandicast import pipeline
from mandicast.config import Settings
from mandicast.db.session import session_scope
from mandicast.serving import publish, registry


def test_quarters_and_origins():
    assert registry.quarter_start(date(2026, 11, 27)) == date(2026, 10, 1)
    assert registry.quarter_end(date(2026, 11, 27)) == date(2026, 12, 31)
    assert registry.quarter_end(date(2026, 2, 3)) == date(2026, 3, 31)
    assert registry.quarter_label(date(2026, 10, 1)) == "2026q4"
    assert publish.monday_on_or_before(date(2026, 11, 25)) == date(2026, 11, 23)
    assert publish.monday_on_or_before(date(2026, 11, 23)) == date(2026, 11, 23)


def test_code_guard_accepts_only_a_verifiable_post_freeze_edit(tmp_path, monkeypatch):
    assert len(registry.check_code_unchanged()) == 64  # the real files: frozen, or a verified documented edit
    real = json.loads(registry.POST_FREEZE.read_text(encoding="utf-8"))
    frozen = json.loads(registry.FROZEN.read_text(encoding="utf-8"))["code_sha256"]
    if registry.code_sha256() == frozen:
        pytest.skip("code identical to the frozen version; nothing to forge")
    forged = {**real, "allowed": [{**real["allowed"][0], "before": real["allowed"][0]["before"] + " (forged)"}]}
    path = tmp_path / "post_freeze_changes.json"
    path.write_text(json.dumps(forged), encoding="utf-8")
    monkeypatch.setattr(registry, "POST_FREEZE", path)
    with pytest.raises(registry.CodeChangedError):
        registry.check_code_unchanged()
    monkeypatch.setattr(registry, "POST_FREEZE", tmp_path / "missing.json")
    with pytest.raises(registry.CodeChangedError):
        registry.check_code_unchanged()


def _primary(days: int, start: date = date(2026, 1, 1)) -> pl.DataFrame:
    rng = np.random.default_rng(1)
    d = [start + timedelta(days=i) for i in range(days)]
    return pl.DataFrame({"series_id": [1] * days, "date": d, "price": 1000 * np.exp(rng.normal(0, 0.02, days).cumsum()),
                         "quality_flag": [False] * days, "input_ok": [True] * days}).with_columns(
        pl.col("series_id").cast(pl.Int32), pl.col("date").cast(pl.Date))


def test_calibration_uses_only_targets_before_the_origin_and_skips_outliers():
    p = _primary(200)
    p = p.with_columns(pl.when(pl.col("date") == date(2026, 3, 10)).then(False).otherwise(pl.col("input_ok"))
                       .alias("input_ok"))
    origin = date(2026, 4, 6)
    preds = pl.DataFrame({"series_id": [1] * 4, "origin": [date(2026, 3, 30), date(2026, 3, 30), date(2026, 3, 2),
                                                             date(2026, 4, 6)],
                          "h": [6, 7, 8, 1], "yhat": [1000.0] * 4}).with_columns(
        pl.col("series_id").cast(pl.Int32), pl.col("h").cast(pl.Int16))
    cal = publish.calibration_set(p, preds, origin, conditional=True)
    # (03-30, h6) → 04-05 < origin: kept; (03-30, h7) → 04-06 = origin: dropped; (03-02, h8) → 03-10 is an outlier
    # target: dropped; origin 04-06 itself: dropped.
    assert cal.select("origin", "h").rows() == [(date(2026, 3, 30), 6)]
    assert "vol" in cal.columns and cal["r"].is_finite().all()


def _cal(origins: list[date], per_origin: int) -> pl.DataFrame:
    rows = [(o, h, v) for o in origins for h in range(1, 15) for v in np.linspace(0.01, 0.05, per_origin)]
    return pl.DataFrame(rows, schema={"origin": pl.Date, "h": pl.Int16, "vol": pl.Float64}, orient="row")\
        .with_columns(pl.lit(0.0).alias("r"))


def test_calibration_window_extends_by_weeks_only_when_needed():
    origin = date(2026, 11, 23)
    mondays = [origin - timedelta(weeks=k) for k in range(1, 105)]
    w, cal, groups = publish.choose_window(_cal(mondays, 30), origin, conditional=True)
    assert w == 364 and int(groups["len"].min()) >= 100  # 52 origins × 10 per (h, tercile)
    w, cal, _ = publish.choose_window(_cal(mondays, 3), origin, conditional=True)  # 1 per (h, tercile) per origin
    assert w == 700 and cal["origin"].n_unique() == 100  # extended by whole weeks until 100 per group
    with pytest.raises(publish.ModelUnavailableError):
        publish.choose_window(_cal(mondays[:90], 3), origin, conditional=True)  # 90 at most, even at 728 days


def test_artifact_integrity_is_checked(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("not a model", encoding="utf-8")
    with pytest.raises(registry.ArtifactIntegrityError, match="SHA-256"):
        registry.load_model(f, "0" * 64)
    with pytest.raises(registry.ArtifactIntegrityError, match="missing"):
        registry.load_model(tmp_path / "absent.txt", "0" * 64)


# --- database --------------------------------------------------------------------------------------------------

@pytest.fixture
def loaded(db, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "REPORT_DIR", tmp_path / "reports")
    s = Settings(database_url=db_url_for_tests(), raw_dir=tmp_path / "raw", commodities=["Soyabean"],
                 request_pause_s=0, manual_aliases_path=tmp_path / "none.csv", page_size=30)
    pipeline.backfill(db, s, ["Soyabean"], client=FakeClient(real_rows()))
    with db.begin() as c:
        cid = c.execute(text("SELECT id FROM commodities WHERE name = 'Soyabean'")).scalar_one()
        sid = c.execute(text("SELECT min(id) FROM price_series")).scalar_one()
        mv = c.execute(text(
            "INSERT INTO model_versions (version, kind, commodity_id, horizon_days, training_cutoff, valid_from, "
            "valid_to, artifact_path, artifact_sha256, code_sha256, params, training, evaluation, interval_method, "
            "status) VALUES ('test-v', 'lightgbm_global', :c, 14, '2025-10-01', '2025-10-01', '2025-12-31', 'x', :z, "
            ":z, '{}', '{}', '{}', 'conditional', 'production') RETURNING id"), {"c": cid, "z": "0" * 64}).scalar_one()
    return {"commodity_id": cid, "series_id": sid, "model_id": mv}


def _prepared(loaded, origin=date(2025, 11, 17), lo_factor=0.9) -> publish.Prepared:
    rows = pl.DataFrame({"series_id": [loaded["series_id"]] * 14, "h": list(range(1, 15)),
                         "last_date": [origin] * 14, "last_price": [5000.0] * 14, "yhat": [5000.0 + h for h in
                                                                                          range(14)],
                         "lo80": [5000.0 * lo_factor] * 14, "hi80": [5500.0] * 14, "lo90": [4400.0] * 14,
                         "hi90": [5700.0] * 14, "volatility_tercile": [1] * 14,
                         "model_yhat": [5000.0 + h for h in range(14)], "served_by": ["model"] * 14}).with_columns(
        pl.col("series_id").cast(pl.Int32), pl.col("h").cast(pl.Int16), pl.col("volatility_tercile").cast(pl.Int16))
    return publish.Prepared("Soyabean", origin, origin + timedelta(days=2), {"id": loaded["model_id"],
                            "version": "test-v"}, 1, rows, {"test": True}, {}, {})



@pytest.mark.db
def test_publication_is_idempotent_and_never_overwrites(loaded, db):
    with session_scope(db) as s:
        first = publish.write(s, _prepared(loaded), job_id=None)
    assert first["status"] == "published" and first["rows_inserted"] == 14
    changed = _prepared(loaded)
    changed.rows = changed.rows.with_columns(pl.col("yhat") * 2)
    with session_scope(db) as s:
        second = publish.write(s, changed, job_id=None)
    assert second == {"status": "already_published", "run_id": first["run_id"], "series_forecast": 1}
    with db.connect() as c:
        points = c.execute(text("SELECT count(*), max(point) FROM forecasts")).one()
        targets = c.execute(text("SELECT bool_and(target_date = issued_on + horizon_days) FROM forecasts")).scalar()
    assert points[0] == 14 and float(points[1]) == 5013.0 and targets is True


@pytest.mark.db
def test_database_rejects_inconsistent_intervals(loaded, db):
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError, match="ck_forecast_interval_order"), session_scope(db) as s:
        publish.write(s, _prepared(loaded, lo_factor=0.5), job_id=None)  # lower80 below lower90


@pytest.mark.db
def test_production_model_must_cover_the_origin(loaded, db):
    with session_scope(db) as s:
        assert publish.production_model(s, "Soyabean", date(2025, 11, 17))["version"] == "test-v"
        with pytest.raises(publish.ModelUnavailableError, match="retrain"):
            publish.production_model(s, "Soyabean", date(2026, 1, 5))
        with pytest.raises(publish.ModelUnavailableError, match="no production model"):
            publish.production_model(s, "Onion", date(2025, 11, 17))


@pytest.mark.db
def test_only_one_production_model_per_commodity(loaded, db):
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError, match="uq_model_production"), db.begin() as c:
        c.execute(text(
            "INSERT INTO model_versions (version, kind, commodity_id, horizon_days, training_cutoff, valid_from, "
            "valid_to, artifact_path, artifact_sha256, code_sha256, params, training, evaluation, interval_method, "
            "status) VALUES ('test-v2', 'lightgbm_global', :c, 14, '2025-10-01', '2025-10-01', '2025-12-31', 'x', "
            ":z, :z, '{}', '{}', '{}', 'conditional', 'production')"), {"c": loaded["commodity_id"], "z": "0" * 64})
