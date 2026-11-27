#!/usr/bin/env bash
# Proves a backup restores (docs/15 §6): verifies its SHA-256, restores it into a temporary database next to the live one,
# compares the row count of every table, and drops the temporary database. The live database is only read.
#
#   scripts/restore_check.sh backups/mandicast-<stamp>.dump
#
# Counts are compared with the live database now, so stop writers first (`docker compose stop worker`) or expect
# differences in tables the scheduler wrote to after the backup (ingestion_jobs, monitoring_snapshots, ...).
# Every command inside the container goes through `sh -c` so Git Bash on Windows cannot rewrite its paths.
set -euo pipefail
cd "$(dirname "$0")/.."

dump="${1:?usage: scripts/restore_check.sh backups/mandicast-<stamp>.dump}"
check_db="mandicast_restore_check"
in_db() { docker compose exec -T db sh -c "$1"; }

cleanup() {
  in_db "rm -f /tmp/restore_check.dump; dropdb -U \"\$POSTGRES_USER\" --if-exists $check_db" || true
}
trap cleanup EXIT

(cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256")

start=$(date +%s)
in_db "dropdb -U \"\$POSTGRES_USER\" --if-exists $check_db && createdb -U \"\$POSTGRES_USER\" $check_db"
docker compose cp "$dump" db:/tmp/restore_check.dump
# Parallel restore needs a seekable file, hence the copy into the container.
in_db "pg_restore -U \"\$POSTGRES_USER\" -d $check_db --jobs=4 --exit-on-error /tmp/restore_check.dump"
restored_s=$(( $(date +%s) - start ))

count_rows() {  # database -> one "table count" line per table in schema public; stops on any SQL error
  local list="SELECT format('SELECT %L, count(*) FROM %I;', tablename, tablename) FROM pg_tables"
  list="$list WHERE schemaname = 'public' ORDER BY tablename"
  # One line for sh: a line break before the pipe would end the command.
  in_db "psql -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d $1 -At -c \"$list\" | psql -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d $1 -At -F ' '" \
    | tr -d '\r'
}
table_count() {
  in_db "psql -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d $1 -At -c \"SELECT count(*) FROM pg_tables WHERE schemaname = 'public'\"" \
    | tr -d '\r'
}

live=$(in_db 'echo "$POSTGRES_DB"' | tr -d '\r')
live_counts=$(count_rows "$live")
restored_counts=$(count_rows "$check_db")
tables=$(table_count "$check_db")

# A comparison of nothing proves nothing: every table must have produced a numeric count.
lines=$(printf '%s\n' "$restored_counts" | grep -c -E '^[a-z_0-9]+ [0-9]+$' || true)
if [ "$tables" -lt 1 ] || [ "$lines" -ne "$tables" ]; then
  echo "restore check failed: counted $lines of $tables tables" >&2
  exit 1
fi
if [ "$live_counts" != "$restored_counts" ]; then
  echo "row counts differ (< live, > restored):" >&2
  diff <(printf '%s\n' "$live_counts") <(printf '%s\n' "$restored_counts") >&2 || true
  exit 1
fi
rows=$(printf '%s\n' "$restored_counts" | awk '{s += $2} END {print s}')
echo "restore OK: $tables tables, $rows rows, counts identical to the live database; restored in $restored_s s"
printf '%s\n' "$restored_counts"
