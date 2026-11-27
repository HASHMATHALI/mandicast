#!/usr/bin/env bash
# Phase 3 hold-out: run ONLY after `python -m mandicast.forecast.freeze` has written frozen_choices.json.
# Every model is run on the hold-out so the report can compare them; the served choice is already frozen.
set -u
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
L=logs/p3_holdout_chain.txt
stamp() { echo "[$(date '+%F %T')] $*" >> "$L"; }
[ -f experiments/forecast/frozen_choices.json ] || { stamp "REFUSED: no frozen choices"; exit 1; }
stamp "START holdout (frozen $(grep -o '"frozen_at": "[^"]*"' experiments/forecast/frozen_choices.json))"
for step in "baselines" "gbm" "stats --model ets" "stats --model arima --sample-per-commodity 60"; do
  stamp "START $step"
  $PY -m mandicast.forecast.run $step --period holdout --confirm-holdout --workers 11 >> logs/p3_holdout_runs.txt 2>&1 \
    && stamp "DONE $step" || { stamp "FAILED $step"; exit 1; }
done
stamp "START final"; $PY -m mandicast.forecast.final >> logs/p3_final.txt 2>&1 && stamp "DONE final" || { stamp "FAILED final"; exit 1; }
$PY -m mandicast.forecast.report >> logs/p3_final.txt 2>&1
stamp "ALL DONE"
