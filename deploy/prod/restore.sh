#!/usr/bin/env bash
# Puts a backup made by backup.sh back: both databases and every stored file. It replaces what is there now.
#
#   ./restore.sh /home/syruss/syruss-backups/20261112T023000Z
#
# The stack must be up (docker compose ... up -d) and nothing should be using it: stop agent, www, site and admin first
# (the server stays up: it writes the files back), start them again after. Try it on a spare machine before the day you need it.
set -euo pipefail
cd "$(dirname "$0")"

from="${1:?which backup folder}"
[ -d "$from" ] || { echo "no such folder: $from" >&2; exit 1; }
( cd "$from" && sha256sum --check --quiet SHA256SUMS ) || { echo "the backup does not match its checksums" >&2; exit 1; }
set -a; . env/stack.env; set +a
compose() { docker compose --env-file env/stack.env "$@"; }

read -r -p "This replaces the databases and files on this machine with $from. Type 'restore' to go on: " answer
[ "$answer" = "restore" ] || { echo "nothing changed"; exit 1; }

echo "$(date -u) restoring the platform database"
compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner < "$from/syruss.pgdump"
echo "$(date -u) restoring Keycloak's database"
compose exec -T keycloak-db pg_restore -U "$KEYCLOAK_DB_USER" -d keycloak --clean --if-exists --no-owner < "$from/keycloak.pgdump"

echo "$(date -u) restoring stored files"
gunzip -c "$from/files.tar.gz" | compose exec -T server python -m app.backup_files restore

echo "$(date -u) restored. Start the services again, then sign in and open a document to check."
