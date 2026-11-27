# MandiCast — Data Dictionary

Version 1.0 (2026-11-27). Sections 1–4 written in Phase 1–2 (source fields verified from the data.gov.in resource
`35985678-0d79-46b4-9ed6-6f13308a1d24`: API field list and downloaded pages); section 5 lists every column of every
table, generated from the database.

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

A **series** is one (commodity, market): variety and grade are row attributes, and `price_series` records the
dominant (variety, grade) of the last year (changed in Phase 2, methodology §1). Eligibility for forecasting is
defined in `06_forecasting_methodology.md`; the fields `eligible_for_forecast` and `eligibility_reason` on
`price_series` carry the result for each series.

## 5. Column reference (all tables, generated)

Every column of the 20 tables at Alembic `0005`, generated from the database by `python -m mandicast.db.schema_doc`.
`tests/test_docs.py` regenerates it from a database built by the migrations and fails if this section differs. What
the tables are for, their keys and constraints: `04_database_schema.md`.

<!-- schema:begin (generated by python -m mandicast.db.schema_doc) -->

**`alert_subscriptions`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `user_id` | integer |  |  |
| `series_id` | integer |  |  |
| `condition_type` | character varying(24) |  |  |
| `threshold` | numeric(14,2) |  |  |
| `active` | boolean |  | true |
| `created_at` | timestamp with time zone |  | now() |
| `last_evaluated_date` | date | yes |  |
| `last_condition` | boolean | yes |  |
| `last_forecast_run_id` | integer | yes |  |

**`commodities`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `name` | text |  |  |
| `agmarknet_code` | text | yes |  |
| `price_unit` | text |  | 'INR_per_quintal' |

**`districts`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `state_id` | integer |  |  |
| `name` | text |  |  |
| `display_name` | text | yes |  |
| `display_seen` | date | yes |  |

**`forecast_runs`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `job_id` | bigint | yes |  |
| `model_version_id` | integer | yes |  |
| `origin` | date |  |  |
| `data_complete_through` | date |  |  |
| `series_eligible` | integer |  |  |
| `series_forecast` | integer |  |  |
| `calibration` | jsonb |  |  |
| `created_at` | timestamp with time zone |  | now() |
| `commodity_id` | integer |  |  |
| `decisions` | jsonb |  | '{}' |
| `drift` | jsonb |  | '{}' |

**`forecasts`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | bigint |  | serial |
| `run_id` | integer |  |  |
| `series_id` | integer |  |  |
| `model_version_id` | integer | yes |  |
| `issued_on` | date |  |  |
| `target_date` | date |  |  |
| `horizon_days` | smallint |  |  |
| `last_date` | date |  |  |
| `last_price` | numeric(14,2) |  |  |
| `point` | numeric(14,2) |  |  |
| `lower80` | numeric(14,2) |  |  |
| `upper80` | numeric(14,2) |  |  |
| `lower90` | numeric(14,2) |  |  |
| `upper90` | numeric(14,2) |  |  |
| `volatility_tercile` | smallint | yes |  |
| `model_point` | numeric(14,2) | yes |  |
| `served_by` | character varying(16) |  | 'model' |

**`ingestion_jobs`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | bigint |  | serial |
| `kind` | character varying(32) |  |  |
| `scope` | jsonb |  | '{}' |
| `status` | character varying(16) |  | 'running' |
| `started_at` | timestamp with time zone |  | now() |
| `finished_at` | timestamp with time zone | yes |  |
| `rows_fetched` | bigint |  | '0' |
| `rows_inserted` | bigint |  | '0' |
| `rows_duplicate` | bigint |  | '0' |
| `rows_rejected` | bigint |  | '0' |
| `quality_report` | jsonb | yes |  |
| `error` | text | yes |  |

**`login_codes`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `user_id` | integer |  |  |
| `code_sha256` | character varying(64) |  |  |
| `created_at` | timestamp with time zone |  | now() |
| `expires_at` | timestamp with time zone |  |  |
| `used_at` | timestamp with time zone | yes |  |

**`markets`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `district_id` | integer |  |  |
| `name` | text |  |  |
| `display_name` | text | yes |  |
| `display_seen` | date | yes |  |

**`model_events`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `commodity_id` | integer |  |  |
| `model_version_id` | integer | yes |  |
| `event` | character varying(16) |  |  |
| `reason` | text | yes |  |
| `details` | jsonb |  | '{}' |
| `created_at` | timestamp with time zone |  | now() |

**`model_versions`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `version` | text |  |  |
| `kind` | character varying(32) |  |  |
| `commodity_id` | integer |  |  |
| `horizon_days` | smallint |  |  |
| `training_cutoff` | date |  |  |
| `valid_from` | date |  |  |
| `valid_to` | date |  |  |
| `artifact_path` | text |  |  |
| `artifact_sha256` | character varying(64) |  |  |
| `code_sha256` | character varying(64) |  |  |
| `params` | jsonb |  |  |
| `training` | jsonb |  |  |
| `evaluation` | jsonb |  |  |
| `interval_method` | character varying(16) |  |  |
| `status` | character varying(16) |  | 'candidate' |
| `created_at` | timestamp with time zone |  | now() |
| `promoted_at` | timestamp with time zone | yes |  |
| `replaced_version_id` | integer | yes |  |
| `gate` | jsonb | yes |  |
| `status_reason` | text | yes |  |
| `mlflow_registry_version` | character varying(32) | yes |  |

**`monitoring_snapshots`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `as_of` | date |  |  |
| `commodity_id` | integer |  |  |
| `day_range` | character varying(8) |  |  |
| `origins` | integer |  |  |
| `points` | integer |  |  |
| `wape_model` | double precision | yes |  |
| `wape_last_value` | double precision | yes |  |
| `wape_served` | double precision | yes |  |
| `coverage80` | double precision | yes |  |
| `coverage90` | double precision | yes |  |
| `direction_moves_ge_2pct` | double precision | yes |  |
| `details` | jsonb |  | '{}' |
| `computed_at` | timestamp with time zone |  | now() |

**`name_aliases`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `entity` | character varying(16) |  |  |
| `parent_key` | text |  | '' |
| `raw_name` | text |  |  |
| `canonical_name` | text |  |  |
| `rule` | character varying(32) |  |  |
| `created_at` | timestamp with time zone |  | now() |
| `last_seen` | date | yes |  |

**`notification_history`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | bigint |  | serial |
| `subscription_id` | integer |  |  |
| `user_id` | integer |  |  |
| `idempotency_key` | character varying(128) |  |  |
| `status` | character varying(16) |  | 'pending' |
| `message` | text |  |  |
| `attempts` | integer |  | 0 |
| `error` | text | yes |  |
| `created_at` | timestamp with time zone |  | now() |
| `sent_at` | timestamp with time zone | yes |  |

**`price_observations`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | bigint |  | serial |
| `series_id` | integer |  |  |
| `date` | date |  |  |
| `variety` | text |  |  |
| `grade` | text |  |  |
| `min_price` | numeric(14,2) | yes |  |
| `max_price` | numeric(14,2) | yes |  |
| `modal_price` | numeric(14,2) | yes |  |
| `source_observation_id` | bigint |  |  |
| `flags` | text[] |  | '{}' |
| `is_primary` | boolean |  | false |

**`price_series`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `commodity_id` | integer |  |  |
| `market_id` | integer |  |  |
| `first_date` | date | yes |  |
| `last_date` | date | yes |  |
| `n_days` | integer |  | 0 |
| `dominant_variety` | text | yes |  |
| `dominant_grade` | text | yes |  |
| `eligible_for_forecast` | boolean |  | false |
| `eligibility_reason` | text | yes |  |
| `eligibility_origin` | date | yes |  |
| `updated_at` | timestamp with time zone |  | now() |

**`raw_pages`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | bigint |  | serial |
| `job_id` | bigint | yes |  |
| `commodity` | character varying(64) |  |  |
| `kind` | character varying(16) |  |  |
| `file_path` | text |  |  |
| `sha256` | character varying(64) |  |  |
| `rows` | integer |  |  |
| `arrival_date` | date | yes |  |
| `request_params` | jsonb |  |  |
| `fetched_at` | timestamp with time zone |  |  |
| `loaded_at` | timestamp with time zone |  | now() |
| `rows_inserted` | integer |  | 0 |

**`source_observations`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | bigint |  | serial |
| `raw_page_id` | bigint |  |  |
| `source_row_hash` | character varying(64) |  |  |
| `arrival_date` | date | yes |  |
| `arrival_date_raw` | text |  |  |
| `state_raw` | text |  |  |
| `district_raw` | text |  |  |
| `market_raw` | text |  |  |
| `commodity_raw` | text |  |  |
| `commodity_code_raw` | text |  |  |
| `variety_raw` | text |  |  |
| `grade_raw` | text |  |  |
| `min_price_raw` | text |  |  |
| `max_price_raw` | text |  |  |
| `modal_price_raw` | text |  |  |
| `first_seen_at` | timestamp with time zone |  | now() |
| `superseded_by_raw_page_id` | bigint | yes |  |

**`states`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `name` | text |  |  |
| `display_name` | text | yes |  |
| `display_seen` | date | yes |  |

**`user_sessions`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `user_id` | integer |  |  |
| `token_sha256` | character varying(64) |  |  |
| `created_at` | timestamp with time zone |  | now() |
| `expires_at` | timestamp with time zone |  |  |
| `last_used_at` | timestamp with time zone | yes |  |

**`users`**

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | integer |  | serial |
| `telegram_chat_id` | bigint |  |  |
| `language` | character varying(8) |  | 'en' |
| `created_at` | timestamp with time zone |  | now() |
| `last_seen_at` | timestamp with time zone |  | now() |

<!-- schema:end -->
