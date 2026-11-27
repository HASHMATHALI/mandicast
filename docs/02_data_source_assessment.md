# MandiCast — Data Source Assessment (Phase 1)

Status: first pass, 2026-11-26. Everything marked **verified** was checked on the official pages or by an
executed API call on that date; the raw responses are kept in `data/raw/probe_2026-11-26/` with the SHA-256
values in §6. Items marked *to verify* are still open.

## 1. Official sources

| Source | What it is | Status |
|---|---|---|
| **Variety-wise Daily Market Prices Data of Commodity** (data.gov.in, resource `35985678-0d79-46b4-9ed6-6f13308a1d24`) | Historical daily wholesale prices (min / max / modal) per market, commodity, variety and grade, reported to AGMARKNET. Publisher: Directorate of Marketing and Inspection (DMI), Ministry of Agriculture and Farmers Welfare | **Primary source.** Verified: 83,329,758 records; history back to **2001** (earliest Onion row 10/01/2001, sorted query); latest arrival date 25/11/2026; updated daily (last update 2026-11-25T23:20:39Z) |
| **Current Daily Price of Various Commodities from Various Markets (Mandi)** (resource `9ef84268-d588-465a-a308-a864a43d0070`) | Same AGMARKNET prices, current day only | Verified: 7,089 records on 26/11/2026 (updated 05:00 UTC). Use for freshness checks and the "current price" view, not for history |
| agmarknet.gov.in portal ("Agmarknet 2.0") | The AGMARKNET site itself. Its "Daily Price and Arrival Report" shows prices and arrival quantities through an interactive form | Verified 2026-11-26: website policy allows reproduction free of charge if accurate, not misleading, and the source is acknowledged as https://agmarknet.gov.in; the disclaimer says the data are reported by the APMCs and DMI is not responsible for errors. Report column labels in the site code: **"Price (Rs./Quintal)"** and **"Arrival (Metric Tonnes)"**. No documented bulk/API access to arrivals, so arrivals are out of scope for v1 |

Rejected or not used as primary data: third-party scrapes (Kaggle "Agmarknet India Commodity Prices", Apify
actors, GitHub scrapers, `mandi-api` blogs). They copy the same official data with unclear provenance and no
licence of their own, and the official API is available. The CEDA (Ashoka University) Agmarknet portal is a
possible cross-check only; its terms are *to verify*.

## 2. Licence (verified, https://www.data.gov.in/Godl)

Both resources are published under the **Government Open Data License – India (GODL)**. It grants a
worldwide, royalty-free, non-exclusive licence to use, adapt, publish and build products and services on the
data, for commercial and non-commercial purposes. Conditions:

- **Attribution** must be published, in the form "[Provider], [Year], [Name of Data], [Repository],
  [Version/Date], [URL]. Published under [Licence]: [URL]". MandiCast will show this on the dashboard and in
  the README, for example: *Directorate of Marketing and Inspection, Ministry of Agriculture and Farmers
  Welfare, 2024, Variety-wise Daily Market Prices Data of Commodity, Open Government Data Platform India,
  accessed dd/mm/yyyy, https://www.data.gov.in/resource/variety-wise-daily-market-prices-data-commodity.
  Published under Government Open Data License – India: https://www.data.gov.in/Godl.* (The resource page
  says "Published On 02/06/2024"; the API metadata says created 2023-09-19. The page date is used.)
- **Non-endorsement**: never suggest the provider endorses MandiCast.
- **No warranty**: the provider is not liable for errors or omissions. MandiCast must surface data-quality
  problems rather than hide them.

## 3. API (verified from the resource's OAS 2.0 page and by calls)

`GET https://api.data.gov.in/resource/35985678-0d79-46b4-9ed6-6f13308a1d24`

| Parameter | Notes |
|---|---|
| `api-key` (required) | Personal key from the portal's "My Account". The portal documents a public sample key that returns **at most 10 records per call**, for trying the API only |
| `format` | `json`, `xml` or `csv` |
| `offset`, `limit` | Pagination. Verified 2026-11-26 with the owner's personal key (`scripts/check_api_key.py`): `limit` 10 / 100 / 1,000 / 5,000 / 10,000 all returned the full page, in 0.5 / 0.5 / 1.2 / 2.9 / 4.6 s. Higher limits not tried |
| `filters[State]`, `filters[District]`, `filters[Commodity]`, `filters[Arrival_Date]` | Exact-match filters. `Arrival_Date` uses `dd/mm/yyyy`. No market or date-range filter is documented |

Fields returned: `Arrival_Date` (date, `dd/mm/yyyy`), `State`, `District`, `Market`, `Commodity`,
`Commodity_Code`, `Variety`, `Grade`, `Min_Price`, `Max_Price`, `Modal_Price`.

Records per arrival date (verified with `filters[Arrival_Date]`, sample key, `limit=1`):

| Arrival date | Records |
|---|---|
| 03/01/2005 | 3,011 |
| 01/01/2010 | 8,246 |
| 02/01/2015 | 10,445 |
| 02/01/2020 | 10,113 |
| 02/01/2025 | 19,250 |
| 25/11/2026 | 17,382 |

**Scale requirement (≥ 1M genuine observations): met by a wide margin.** The resource holds 83.3M rows; any
reasonable subset (a few commodities across all markets, 2015–2026) is several million rows.

## 4. Access

Bulk ingestion uses the **owner's own data.gov.in API key** (free registration), stored in `mandicast/.env` as
`DATA_GOV_IN_API_KEY` (see `.env.example`); it is never committed or logged. Verified 2026-11-26: the personal
key works and returns up to 10,000 records per call, so 1M rows is about 100 calls (~8 min at the measured
speed, sequential). The public sample key returned HTTP 429 (rate-limited) the same morning, so it is not
usable even for tests.

Client note (verified): requests with Python's default `Python-urllib` User-Agent got HTTP 502 and a read
timeout, while the same requests from curl, and from Python with an explicit `User-Agent: MandiCast-ingest/0.1`,
succeeded. The ingestion client sends that header and no personal information.

## 5. Data-quality issues (measured counts per commodity: `reports/profile/PROFILE_SUMMARY.md`)

| Issue | Evidence | Handling planned |
|---|---|---|
| Type drift | 2006 records store prices and `Commodity_Code` as strings (`'3030'`); 2026 records as integers (`3975`) | Parse explicitly, keep the original value in the raw table |
| Schema differences between resources | Historical resource uses `Title_Case` field names, current-day resource uses lower case | One adapter per resource, mapped to a single schema |
| Renamed or misspelled states | Both `Kerala` and `Keralam`, both `Uttarakhand` and `Uttrakhand` in the state list | State alias table, keep the source spelling |
| Commodity names differing only by spacing | e.g. `Arhar (Tur/Red Gram)(Whole)` and `Arhar(Tur/Red Gram)(Whole)` | Normalise names, prefer `Commodity_Code` where present; verify codes are stable |
| Market names with suffixes | `Mansa APMC`, `Naravarikuppam(Uzhavar Sandhai )` | Market identity = (state, district, market) after whitespace normalisation; alias table built from data |
| No arrival quantity | Neither API resource has an arrivals field | Arrivals are out of scope unless an official bulk source is verified (§1) |
| Price unit not stated in the API | The Agmarknet 2.0 report labels prices "Price (Rs./Quintal)" (verified in the site's front-end code). Observed levels fit: Onion modal Rs 1,650 in 2005 = Rs 16.5/kg | Display as ₹/quintal with a ₹/kg helper. Some commodities outside the sample (eggs, coconuts, flowers) may use other units; check before adding them |
| Coverage grows over time | 3k rows/day in 2005 vs 17–19k in 2025–26 | Forecast only series with enough history (methodology doc) |
| Zero min/max means "not reported" | Onion: 122,479 rows have min = max = 0 with a positive modal; another 6,842 have one bound at 0 | Treated as missing bounds (flag `bounds_not_reported`), not as price violations; the modal price is still used |
| Real range violations are rare | Onion: min > max 4 rows, modal < min 312, modal > max 379 (where both bounds are positive) | Flagged, excluded by default |
| Absurd values | Onion modal maximum ₹917,588,483/quintal, minimum ₹0.01; 99.9th percentile ₹10,000 | Local-median outlier flag (after the boundary repair: 3,940 Onion points beyond 3× / ⅓; 349 beyond 10× / ⅒) |
| Same-day conflicting reports | Onion: 3,190 (mandi, variety, grade, date) keys with different modal prices | Kept, flagged `conflict`; one value per mandi-day by the rule in the methodology |
| Variety labels renamed | Onion "Red": 57k rows in 2025, 10k in 2026; "Bellary": 30k in 2024, 65k in 2026 | Series keyed by (commodity, mandi), not variety |
| Case / whitespace variants | Districts `BONGAIGAON`/`Bongaigaon`, ` Jhunjhunu`; 31 Onion market names with a trailing space (`Lasalgaon(Niphad) `) | Trim and case-fold for matching; alias table keeps every source spelling |
| **Mass rename of mandis, 1–4 Nov 2025** | AGMARKNET appended " APMC" to almost every market name (`Sehore` → `Sehore APMC`, `Indore(F&V)` → `Indore(F&V) APMC`). Old names stop on 4 Nov 2025; nearly all currently active names first appear in 2025–26. For 83–89% of active "APMC" names (per commodity) an old name differing only by the suffix exists in the same district; on the ~2 overlap days their modal prices are identical in 96–100% of rows | Alias rule `X APMC` = `X` within (state, district), verified by the overlap; overlap days de-duplicated. The other 11–17% (e.g. `Bengaluru APMC`, no exact predecessor) need reviewed manual mappings in Phase 2, or keep a short history. Without the alias, every series would lose its pre-Nov-2025 history |
| Latest days incomplete at download | Onion rows on 25 Nov 2026: 547; 24 Nov: 633; other weekdays ~740–800 | Last dates treated as provisional (methodology §2) |

## 6. Probe files (raw API responses, untrusted input, git-ignored)

| File | SHA-256 |
|---|---|
| `all.json` | `0aff65bda513a607701aa086713019f4a5a86d51edaf287cb0a521ee3b68aff6` |
| `current_daily.json` | `82f2187667af90290f00146f16206167ede3eacd0bdcf7102e5e5196fc120137` |
| `date_03-01-2005.json` | `e2586a4f220339fd5bcc054237f0675d4154758355be37c99d083696f01a4b4f` |
| `date_01-01-2010.json` | `07f961af469ef591febc7491acbe1e4d0cc6cbf36db0cbb9ac599c2c0c9e135a` |
| `date_02-01-2015.json` | `e1179db5be720d3a04b79d187bb31bd66027ba1acb2405af6bc8f9631e0c858d` |
| `date_02-01-2020.json` | `a310671f9163acfe732ab0190b56d2bfdc9142b4c64e39378cb62a5fea6efbae` |
| `date_02-01-2025.json` | `eabbf08a5eb82787c618be2c2d43da4a778340e090fbb3a22c57e3cf46f3b7be` |
| `date_25-11-2026.json` | `56402c00153d2f2b8a9e6aa446c579b062c3a36b11c1aa23f513a928d72ba705` |

## 7. Open items
1. ~~Rate limits under sustained use~~ Observed 2026-11-26: 2,961 data requests (1,485 pages of 10,000 rows +
   1,476 per-date re-fetches), sequential with a 0.5–1 s pause: no HTTP 429; two HTTP 504 from the server; the
   other 53 failed attempts were local network errors (32 DNS failures, connection resets, timeouts) during
   Wi-Fi drops. All recovered by retries. No documented limit was found; the ingestion keeps the pause and backoff.
2. ~~`Commodity_Code` stability~~ Verified: one code per commodity across all rows (Onion 23, Tomato 78, Potato 24,
   Wheat 1, Soyabean 13).

## 8. Bulk download behaviour (verified 2026-11-26)

| Check | Result |
|---|---|
| Rows per commodity (`filters[Commodity]`) | Onion 3,425,173; Tomato 3,055,643; Potato 3,492,438; Wheat 3,559,803; Soyabean 1,294,978 (14.8M in total) |
| Deep pagination | Works: `offset` 10,000 / 50,000 / 1,000,000 all return rows |
| Sorting | `sort[Arrival_Date]=asc` / `desc` work (not in the OAS page, verified by calls). Without a sort the order is arbitrary |
| Repeatability | The same sorted CSV page requested twice was byte-identical (10,000 rows, 719,507 bytes) |
| Page boundaries | Rows 9,995–9,999 of page 1 equal the first five rows of a request at offset 9,995 |
| Speed | 10,000-row CSV page in 2–5 s; about 4.6 s per page including a 0.5 s pause between requests |

Downloader: `scripts/fetch_agmarknet_history.py` (resumable, per-page SHA-256 manifest, retries with backoff,
re-checks the total at the end and fetches rows added during the download, never logs the key).
| **Ties at page boundaries** | Rows with the same sort key (arrival date) can move between pages requested at different times, because the index is live. Onion: the boundary on 02/11/2026 had 9 rows duplicated and 9 rows missing; all other boundaries matched |

Fix (verified on Onion): every date that is split across two pages (341 of 342 Onion boundaries) is re-fetched
in full with `filters[Arrival_Date]`, and those per-date files replace the page rows for that date. Onion
result: 164,911 rows on boundary dates both ways; the re-fetch recovered the 9 missing rows and removed the 9
duplicates; the final row count equals the API total (3,425,173). The remaining single exact duplicate
(Tripura, 02/11/2025) is in the source itself.

## 9. API behaviour found in Phase 2 (verified on the live API, 2026-11-26)

| Behaviour | Handling |
|---|---|
| A date with no records (e.g. today before any report) returns HTTP 200 with an **empty body**, not a header | Counted as "no rows yet", never stored or applied |
| Exact-date requests (`filters[Arrival_Date]`, no sort) return rows in a **different order** on each request | Snapshots are compared by an order-independent content hash |
| Repeated daily fetches of 18–25 Nov 2026 returned the same rows as the morning download | No late reports in that window; late reporting is still expected (§5) and handled by re-fetching 7 days |

