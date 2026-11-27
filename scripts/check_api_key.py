"""Check the data.gov.in API key in .env and measure how many records one call can return.

Usage (from the mandicast/ folder):  python -I scripts/check_api_key.py

The key is read from .env and never printed or logged. Only record counts and timings are shown.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = "35985678-0d79-46b4-9ed6-6f13308a1d24"
BASE = f"https://api.data.gov.in/resource/{RESOURCE}"
SAMPLE_KEY_PREFIX_AND_SUFFIX = ("579b464db66ec23bdd000001", "cdd3946e44ce4aad7209ff7b23ac571b")


def read_key() -> str:
    env = ROOT / ".env"
    if not env.exists():
        sys.exit("No .env file. Copy .env.example to .env and add DATA_GOV_IN_API_KEY.")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATA_GOV_IN_API_KEY="):
            key = line.split("=", 1)[1].strip()
            if not key:
                sys.exit("DATA_GOV_IN_API_KEY is empty in .env.")
            if key.endswith(SAMPLE_KEY_PREFIX_AND_SUFFIX[1]):
                sys.exit("This is the portal's public sample key (10 records per call), not a personal key.")
            return key
    sys.exit("DATA_GOV_IN_API_KEY not found in .env.")


def fetch(key: str, **params) -> tuple[dict, float]:
    query = urllib.parse.urlencode({"api-key": key, "format": "json", **params})
    t = time.perf_counter()
    req = urllib.request.Request(f"{BASE}?{query}", headers={"User-Agent": "MandiCast-ingest/0.1"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - fixed https host
        body = json.loads(resp.read().decode("utf-8"))
    return body, time.perf_counter() - t


def main() -> None:
    key = read_key()
    for limit in (10, 100, 1000, 5000, 10000):
        try:
            body, secs = fetch(key, limit=limit, offset=0, **{"filters[Arrival_Date]": "02/01/2025"})
        except urllib.error.HTTPError as exc:
            print(f"limit={limit}: HTTP {exc.code}")
            continue
        except TimeoutError:
            print(f"limit={limit}: timed out after 120 s")
            continue
        print(f"limit={limit:>5}: status={body.get('status')} returned={len(body.get('records', []))} "
              f"total_for_date={body.get('total')} in {secs:.1f}s")


if __name__ == "__main__":
    main()
