#!/usr/bin/env bash
# Phase 3 development-period runs, in order. Hold-out is not touched here.
set -u
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
L=logs/p3_dev_chain.txt
stamp() { echo "[$(date '+%F %T')] $*" >> "$L"; }
stamp "START baselines";  $PY -m mandicast.forecast.run baselines --period dev >> logs/p3_baselines_dev.txt 2>&1 && stamp "DONE baselines" || { stamp "FAILED baselines"; exit 1; }
stamp "START ets";        $PY -m mandicast.forecast.run stats --model ets --period dev --workers 11 >> logs/p3_ets_dev.txt 2>&1 && stamp "DONE ets" || { stamp "FAILED ets"; exit 1; }
stamp "START arima";      $PY -m mandicast.forecast.run stats --model arima --period dev --workers 11 >> logs/p3_arima_dev.txt 2>&1 && stamp "DONE arima" || { stamp "FAILED arima"; exit 1; }
stamp "ALL DONE"
