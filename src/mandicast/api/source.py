"""Attribution (GODL-India conditions, docs/02 §2), units and the forecast disclaimer, shown in API responses."""

from __future__ import annotations

SOURCE = {
    "provider": "Directorate of Marketing and Inspection, Ministry of Agriculture and Farmers Welfare, Government of "
                "India",
    "dataset": "Variety-wise Daily Market Prices Data of Commodity (AGMARKNET)",
    "repository": "Open Government Data Platform India",
    "url": "https://www.data.gov.in/resource/variety-wise-daily-market-prices-data-commodity",
    "licence": "Government Open Data License - India",
    "licence_url": "https://www.data.gov.in/Godl",
    "notice": "Prices as reported by the market committees (APMCs), with MandiCast quality flags. MandiCast is not "
              "endorsed by the data provider, and the provider gives no warranty.",
}
UNIT = "INR_per_quintal"
DISCLAIMER = ("Estimate from past prices only (no weather, arrivals or policy data). Not a guarantee of future prices: "
              "actual prices can fall outside the range.")
# Flags that make a value unreliable; such rows are left out unless include_flagged=true. Other flags are
# informational (e.g. bounds_not_reported, conflict) and the row is shown with them.
EXCLUDED_BY_DEFAULT = ["local_outlier", "non_numeric_price", "zero_or_negative_price", "min_gt_max",
                       "modal_outside_range", "future_date", "duplicate_after_normalisation"]
