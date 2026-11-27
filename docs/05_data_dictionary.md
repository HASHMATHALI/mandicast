# MandiCast — Data Dictionary

Version 0.1 (Phase 1, 2026-11-26). Source fields verified from the data.gov.in resource
`35985678-0d79-46b4-9ed6-6f13308a1d24` (API field list and downloaded pages).

## 1. Source fields (as received, stored unchanged in `source_observations`)

| Source field | Type in API | Example | Meaning | Notes from the data |
|---|---|---|---|---|
| `Arrival_Date` | date, text `dd/mm/yyyy` | `25/11/2026` | Day the price was recorded at the mandi | Parsed with an explicit format; never guessed |
| `State` | text | `Maharashtra` | State / UT of the mandi | Renamed and misspelled variants occur (`Kerala`/`Keralam`, `Uttarakhand`/`Uttrakhand`) |
| `District` | text | `Nashik` | District of the mandi | Spellings vary over time |
| `Market` | text | `Mansa APMC` | Mandi (APMC market or sub-yard) name | Suffixes and spacing vary (`APMC`, `(Uzhavar Sandhai )`) |
| `Commodity` | text | `Onion` | Commodity name | Some names differ only in spacing (`Arhar (Tur/Red Gram)(Whole)` / `Arhar(Tur/Red Gram)(Whole)`) |
| `Commodity_Code` | text in old rows, number in new rows | `23` | AGMARKNET commodity code | Stability across names and years is checked in profiling |
| `Variety` | text | `Bellary`, `Other` | Variety as reported | `Other` is common |
| `Grade` | text | `FAQ`, `Local` | Quality grade | FAQ = Fair Average Quality |
| `Min_Price` | text in old rows, number in new rows | `2862` | Lowest price of the day | **₹ per quintal** (Agmarknet report label "Price (Rs./Quintal)") |
| `Max_Price` | same | `3030` | Highest price of the day | ₹ per quintal |
| `Modal_Price` | same | `2862` | Most frequent price of the day | ₹ per quintal. The forecasting target |

## 2. Cleaned fields (`price_observations`, `price_series` and dimensions)

| Field | Type | Derived from | Rule |
|---|---|---|---|
| `date` | DATE | `Arrival_Date` | `strptime('%d/%m/%Y')`; unparseable → row rejected with reason `invalid_date` |
| `state_id`, `district_id`, `market_id` | INT | `State`, `District`, `Market` | Canonical key = name with whitespace collapsed and case-folded, within the parent area; Keralam → Kerala, Uttrakhand → Uttarakhand; for mandis the " APMC" suffix removed (AGMARKNET renamed markets on 1–4 Nov 2025; profile: identical overlap prices in 96–100% of rows); reviewed manual mappings override. Each source spelling is recorded in `name_aliases` with a rule label (`manual`, `state_rename`, `apmc_suffix`, `whitespace`, `exact`). Display name = most recently seen spelling (`display_name`, `display_seen`). Unmapped names create new entities (never merged by guessing) |
| `commodity_id` | INT | `Commodity`, `Commodity_Code` | Code first when stable, name aliases otherwise |
| `variety`, `grade` | TEXT | `Variety`, `Grade` | Whitespace-trimmed; case kept |
| `min_price`, `max_price`, `modal_price` | NUMERIC(12,2) | price fields | Parsed explicitly; non-numeric → NULL with flag `non_numeric_price`, never replaced |
| `flags` | TEXT[] | checks below | Zero or more quality flags |
| `is_primary` | BOOL | duplicates and conflicts | One primary row per series and day |

## 3. Quality flags (implemented in `src/mandicast/clean/build.py`, tested in `tests/test_load_and_clean.py`)

Flags are stored on each cleaned row (`price_observations.flags`). Rows marked *excluded* never become the
primary value of a day; nothing is deleted and no value is replaced.

| Flag | Condition | Effect |
|---|---|---|
| `non_numeric_price` | A price field is not empty but is not a number | Excluded; the unparseable value is NULL in the cleaned row (the source text is kept in `source_observations`) |
| `zero_or_negative_price` | Modal price ≤ 0 | Excluded |
| `bounds_not_reported` | Min and/or max is 0 while the modal is positive (Onion: 129,321 rows) | Min/max stored as NULL; the modal price is used normally |
| `min_gt_max` | Both bounds positive and `min > max` | Excluded |
| `modal_outside_range` | Both bounds positive and the modal is below the min or above the max | Excluded |
| `future_date` | Arrival date after the processing date | Excluded |
| `duplicate_after_normalisation` | Same series, day, variety, grade and prices as an earlier row (e.g. the old and new name of a renamed mandi reporting the same day) | Excluded (the first one is kept) |
| `conflict` | Same series, day, variety and grade but different modal prices | Kept; one of them may be primary |
| `day_has_conflicting_reports` | On the primary row: the day has conflicting rows | Counts against eligibility |
| `day_has_invalid_rows` | On the primary row: the day also has excluded rows | Counts against eligibility |
| `local_outlier` | On the primary row: modal more than 3× or less than ⅓ of the median of the **previous** 15 primary prices (at least 5) | Kept and shown, marked; counts against eligibility. Trailing only, so it can be used in walk-forward training without leakage (Phase 1 profiling used a centred window; the trailing rule replaced it before any modelling) |

Exact repeats of a source row are not flags: they share the row hash and are stored once (`rows_duplicate` in
the job record).

**Primary row of a day** (one per series and date): among non-excluded rows, the (variety, grade) reported most
often for that mandi in the previous 365 days; ties → the row with the lower-median modal price, then the lowest
source id. Only past data is used.

## 4. Series and eligibility

A **series** is one (commodity, market, variety, grade). Eligibility for forecasting is defined in
`06_forecasting_methodology.md`; the fields `eligible_for_forecast` and `eligibility_reason` on
`price_series` carry the result for each series.
