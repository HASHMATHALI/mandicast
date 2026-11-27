#!/usr/bin/env bash
# Waits for the part-1 chain to finish ETS; stops it before ARIMA and runs part 2 (LightGBM alone, then ARIMA).
set -u
cd "$(dirname "$0")/.."
L=logs/p3_dev_chain.txt
until grep -qE "START arima|FAILED" "$L"; do sleep 15; done
if grep -q "FAILED" "$L"; then echo "part 1 failed; not continuing" >> "$L"; exit 1; fi
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*run_phase3_dev.sh*' -or \$_.CommandLine -like '*--model arima*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo "[$(date '+%F %T')] STOPPED part 1 after ets (arima moved to part 2, after gbm)" >> "$L"
bash scripts/run_phase3_dev_part2.sh
