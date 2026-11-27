#!/usr/bin/env bash
# Database backup (docs/15 §6). Runs pg_dump inside the db container over its local socket, so no password appears on
# a command line or in this shell. Writes backups/mandicast-<UTC time>.dump (custom format, compressed) with a SHA-256
# file, checks that the archive can be read, and keeps the newest BACKUP_KEEP (default 7) backups: they contain users'
# Telegram chat ids and alerts, so they are not kept indefinitely.
#
#   scripts/backup.sh [backup-dir]
#   scripts/restore_check.sh backups/mandicast-<stamp>.dump      (proves the backup restores)
set -euo pipefail
cd "$(dirname "$0")/.."

dir="${1:-backups}"
keep="${BACKUP_KEEP:-7}"
mkdir -p "$dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$dir/mandicast-$stamp.dump"

start=$(date +%s)
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=6' \
  > "$out.partial"
mv "$out.partial" "$out"
(cd "$dir" && sha256sum "$(basename "$out")" > "$(basename "$out").sha256")

# The archive's table of contents must be readable (catches truncated or corrupt files).
entries=$(docker compose exec -T db pg_restore --list < "$out" | grep -c "TABLE DATA" || true)
if [ "$entries" -lt 1 ]; then
  echo "backup unreadable: $out" >&2
  exit 1
fi

# Retention: newest $keep backups.
ls -1t "$dir"/mandicast-*.dump | tail -n +$((keep + 1)) | while read -r old; do
  rm -f -- "$old" "$old.sha256"
done

echo "$out: $(du -h "$out" | cut -f1), $entries tables with data, $(( $(date +%s) - start )) s"
