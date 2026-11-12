#!/usr/bin/env bash
# Backs up everything Syruss keeps: both databases (the platform's and Keycloak's) and every stored file.
#
#   ./backup.sh            one backup into $BACKUP_DIR/<UTC time>/, then backups older than $KEEP_DAYS are removed
#
# Run it from this folder (it reads env/stack.env), from cron, for example every night at 02:30:
#   30 2 * * * cd /home/syruss/syruss-deploy && ./backup.sh >> backup.log 2>&1
# Copy $BACKUP_DIR off the machine as well (another region's object storage, rclone, restic): a backup on the same disk
# does not survive losing the disk. restore.sh puts one back; try it on a spare machine before you need it.
set -euo pipefail
cd "$(dirname "$0")"

BACKUP_DIR="${BACKUP_DIR:-$HOME/syruss-backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
set -a; . env/stack.env; set +a
compose() { docker compose --env-file env/stack.env "$@"; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/$stamp"
mkdir -p "$out"
chmod 700 "$BACKUP_DIR" "$out"   # the dumps hold customers' data and password hashes

echo "$(date -u) backing up to $out"
# Custom format: compressed, and restorable one table at a time if it ever comes to that
compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner > "$out/syruss.pgdump"
compose exec -T keycloak-db pg_dump -U "$KEYCLOAK_DB_USER" -d keycloak --format=custom --no-owner > "$out/keycloak.pgdump"

# Stored files (documents, invoices, payment slips), read through the server, which already reaches storage
compose exec -T server python -m app.backup_files dump | gzip > "$out/files.tar.gz"

for f in syruss.pgdump keycloak.pgdump files.tar.gz; do
  [ -s "$out/$f" ] || { echo "backup failed: $f is empty" >&2; exit 1; }
done
( cd "$out" && sha256sum syruss.pgdump keycloak.pgdump files.tar.gz > SHA256SUMS )
echo "$(date -u) done: $(du -sh "$out" | cut -f1)"

find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$KEEP_DAYS" -exec rm -rf {} +
