#!/usr/bin/env bash
# Phase 3 development runs, part 2 (after ETS): LightGBM alone on the CPU, then ARIMA.
set -u
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
L=logs/p3_dev_chain.txt
stamp() { echo "[$(date '+%F %T')] $*" >> "$L"; }
stamp "START gbm";   $PY -m mandicast.forecast.run gbm --period dev >> logs/p3_gbm_dev.txt 2>&1 && stamp "DONE gbm" || { stamp "FAILED gbm"; exit 1; }
stamp "START arima"; $PY -m mandicast.forecast.run stats --model arima --period dev --workers 11 >> logs/p3_arima_dev.txt 2>&1 && stamp "DONE arima" || { stamp "FAILED arima"; exit 1; }
stamp "ALL DONE (part 2)"
