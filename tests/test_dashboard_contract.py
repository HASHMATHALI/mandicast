"""The dashboard's TypeScript types are generated from a saved copy of the API's OpenAPI document
(dashboard/src/api/openapi.json, written by `npm run gen:api`). This test fails when the API's schema changes without
regenerating them, so the dashboard cannot drift silently from the backend (docs/14 §2)."""

from __future__ import annotations

import json
from pathlib import Path

from mandicast.api.app import create_app
from mandicast.config import get_settings

SNAPSHOT = Path(__file__).resolve().parents[1] / "dashboard" / "src" / "api" / "openapi.json"


def test_dashboard_schema_snapshot_matches_the_api():
    app = create_app(get_settings(), use_redis=False)
    try:
        current = json.loads(json.dumps(app.openapi()))
    finally:
        app.state.engine.dispose()
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert current == saved, ("API schema differs from the dashboard's copy: start the API and run "
                              "`npm run gen:api` in dashboard/, then check the type errors (`npm run typecheck`)")
