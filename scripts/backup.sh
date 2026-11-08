#!/usr/bin/env bash
# Backup of the local stack (M10, doc 03 §6.5, docs/operations/backup-restore.md).
#
#   scripts/backup.sh <dir> [--with-observability]      (make backup DIR=… [OBS=1])
#
# Writes into <dir>:
#   leave.dump keycloak.dump n8n.dump [langfuse.dump]   pg_dump -Fc of each database (custom format, compressed)
#   seaweed.tar.gz n8n.tar.gz [clickhouse.tar.gz]       the object store, n8n's home, (Langfuse's ClickHouse data)
#   manifest.json                                       date, commit, file sizes, row counts used by the restore check
#
# Not included, on purpose: the `models` volume (re-downloadable with `make models`), Mailpit (captured test mail),
# and deploy/.env. Keep deploy/.env with the backup yourself: APP_KEK, VAULT_HMAC_KEY/VAULT_KEK and N8N_ENCRYPTION_KEY
# decrypt what the dumps contain (supporting documents, OAuth tokens, the pseudonym vault, n8n credentials). A backup
# restored with different keys is unreadable. The script never copies or prints secrets.
#
# PostgreSQL must be running. SeaweedFS and n8n (and ClickHouse with --with-observability) are stopped while their
# volumes are copied, so the copy is consistent, then started again. The api keeps running; uploads fail for the
# few seconds the object store is down.
set -euo pipefail

DIR=${1:?usage: scripts/backup.sh <dir> [--with-observability]}
OBS=0
[[ "${2:-}" == "--with-observability" ]] && OBS=1
ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=(docker compose -f "$ROOT/deploy/compose.yaml" --env-file "$ROOT/deploy/.env")
PROJECT=leave-assistant
PGIMAGE=pgvector/pgvector:0.8.7-pg18   # already present locally; has tar and gzip
mkdir -p "$DIR"
DIR=$(cd "$DIR" && pwd)

log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }

running() { [[ -n "$(docker ps -q --filter "name=^${PROJECT}-$1-1$")" ]]; }

running postgres || { echo "postgres is not running: start the stack first (make up-staged)" >&2; exit 1; }

databases=(leave keycloak n8n)
(( OBS )) && databases+=(langfuse)
for db in "${databases[@]}"; do
  exists=$("${COMPOSE[@]}" exec -T postgres psql -U postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$db'")
  if [[ "$exists" != "1" ]]; then log "database $db does not exist, skipped"; continue; fi
  log "dumping database $db"
  "${COMPOSE[@]}" exec -T postgres pg_dump -U postgres -Fc -d "$db" > "$DIR/$db.dump"
done

copy_volume() {  # <volume> <archive> <services to stop while copying…>
  local volume=$1 archive=$2; shift 2
  local stopped=()
  for service in "$@"; do
    if running "$service"; then "${COMPOSE[@]}" stop "$service" >/dev/null 2>&1; stopped+=("$service"); fi
  done
  log "archiving volume $volume"
  # streamed through stdout, so the archive belongs to the calling user (Docker may remap the container's root)
  docker run --rm -v "${PROJECT}_${volume}:/data:ro" "$PGIMAGE" tar czf - -C /data . > "$DIR/$archive"
  for service in "${stopped[@]}"; do "${COMPOSE[@]}" start "$service" >/dev/null 2>&1; done
}

copy_volume seaweed seaweed.tar.gz seaweedfs
docker volume inspect "${PROJECT}_n8n" >/dev/null 2>&1 && copy_volume n8n n8n.tar.gz n8n
(( OBS )) && docker volume inspect "${PROJECT}_clickhouse-data" >/dev/null 2>&1 \
  && copy_volume clickhouse-data clickhouse.tar.gz langfuse-worker langfuse-web clickhouse

count() {
  "${COMPOSE[@]}" exec -T postgres psql -U postgres -d leave -Atc "SELECT count(*) FROM $1" 2>/dev/null || echo null
}
log "writing manifest"
{
  printf '{\n  "date": "%s",\n  "commit": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  printf '  "with_observability": %s,\n  "files": {' "$([[ $OBS == 1 ]] && echo true || echo false)"
  first=1
  for f in "$DIR"/*.dump "$DIR"/*.tar.gz; do
    [[ -e "$f" ]] || continue
    (( first )) || printf ','
    printf '\n    "%s": %s' "$(basename "$f")" "$(stat -c %s "$f")"
    first=0
  done
  printf '\n  },\n  "rows": {'
  first=1
  for table in platform.tenants app.employees app.leave_requests app.leave_ledger_entries app.documents app.document_chunks \
               app.pii_vault audit.audit_log app.conversations; do
    (( first )) || printf ','
    printf '\n    "%s": %s' "$table" "$(count "$table")"
    first=0
  done
  printf '\n  }\n}\n'
} > "$DIR/manifest.json"
log "backup complete: $DIR"
echo "Keep deploy/.env (APP_KEK, VAULT_*, N8N_ENCRYPTION_KEY) together with this backup; it is not copied."
