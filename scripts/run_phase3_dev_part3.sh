#!/usr/bin/env bash
# Phase 3 development runs, part 3: LightGBM again (state-code fix), alone on the CPU; then ARIMA.
set -u
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
L=logs/p3_dev_chain.txt
stamp() { echo "[$(date '+%F %T')] $*" >> "$L"; }
mv logs/p3_gbm_dev.txt logs/p3_gbm_dev_first_run_state_bug.txt 2>/dev/null
stamp "START gbm (rerun after state-code fix)"; $PY -m mandicast.forecast.run gbm --period dev >> logs/p3_gbm_dev.txt 2>&1 && stamp "DONE gbm" || { stamp "FAILED gbm"; exit 1; }
stamp "START arima"; $PY -m mandicast.forecast.run stats --model arima --period dev --workers 11 >> logs/p3_arima_dev.txt 2>&1 && stamp "DONE arima" || { stamp "FAILED arima"; exit 1; }
stamp "ALL DONE (part 3)"
