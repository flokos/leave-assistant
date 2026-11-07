#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh (M10, docs/operations/backup-restore.md).
#
#   scripts/restore.sh <dir> [--yes]          (make restore DIR=…)
#
# Replaces the current data: every application container is stopped; only PostgreSQL runs while the databases are
# dropped, recreated with their owners from deploy/postgres/init/01-init.sh and restored; the volumes in the backup
# (seaweed, n8n, clickhouse-data) are emptied and refilled. The roles must exist, which they do on any volume
# initialised by the init script with the same deploy/.env. deploy/.env must hold the keys the backup was made with.
# Afterwards: `make up-staged` (or `make up`), then `scripts/restore.sh --check <dir>` compares the row counts.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE=(docker compose -f "$ROOT/deploy/compose.yaml" --env-file "$ROOT/deploy/.env")
PROJECT=leave-assistant
PGIMAGE=pgvector/pgvector:0.8.7-pg18
ALL_PROFILES=(--profile core --profile automation --profile observability --profile eval)

log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }

psql_q() { "${COMPOSE[@]}" exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 "$@"; }

check() {  # compare the row counts of a running stack with the manifest
  local dir=$1 failed=0 table expected actual
  while read -r table expected; do
    actual=$(psql_q -d leave -Atc "SELECT count(*) FROM $table" < /dev/null 2>/dev/null || echo null)
    if [[ "$actual" == "$expected" ]]; then log "ok   $table $actual"; else log "DIFF $table backup=$expected now=$actual"; failed=1; fi
  done < <(python3 -c 'import json,sys; [print(t, c) for t, c in json.load(open(sys.argv[1]))["rows"].items()]' "$dir/manifest.json")
  return $failed
}

if [[ "${1:-}" == "--check" ]]; then check "${2:?usage: scripts/restore.sh --check <dir>}"; exit; fi

DIR=$(cd "${1:?usage: scripts/restore.sh <dir> [--yes]}" && pwd)
[[ -f "$DIR/manifest.json" && -f "$DIR/leave.dump" ]] || { echo "$DIR is not a backup (manifest.json, leave.dump)" >&2; exit 1; }
if [[ "${2:-}" != "--yes" ]]; then
  read -r -p "This replaces the current databases and volumes with the backup in $DIR. Type 'restore': " answer
  [[ "$answer" == "restore" ]] || { echo "aborted"; exit 1; }
fi

log "stopping the stack"
"${COMPOSE[@]}" "${ALL_PROFILES[@]}" stop >/dev/null 2>&1 || true
"${COMPOSE[@]}" --profile core up -d --wait postgres >/dev/null

declare -A OWNERS=([leave]=postgres [keycloak]=keycloak [n8n]=n8n [langfuse]=langfuse)
for db in leave keycloak n8n langfuse; do
  [[ -f "$DIR/$db.dump" ]] || { log "no $db.dump in the backup, $db left as it is"; continue; }
  log "restoring database $db"
  psql_q -d postgres -c "DROP DATABASE IF EXISTS $db WITH (FORCE)" >/dev/null
  psql_q -d postgres -c "CREATE DATABASE $db OWNER ${OWNERS[$db]}" >/dev/null
  if [[ $db == leave ]]; then  # database-level grants of the init script are not part of pg_dump
    psql_q -d postgres -c "GRANT CREATE, CONNECT ON DATABASE leave TO app_owner; GRANT CONNECT ON DATABASE leave TO app_rls, langgraph_rw" >/dev/null
  fi
  "${COMPOSE[@]}" exec -T postgres pg_restore -U postgres -d "$db" --exit-on-error < "$DIR/$db.dump"
done

restore_volume() {  # <archive> <volume>
  local archive=$1 volume=$2
  [[ -f "$DIR/$archive" ]] || return 0
  log "restoring volume $volume"
  docker volume create "${PROJECT}_${volume}" >/dev/null
  docker run --rm -v "${PROJECT}_${volume}:/data" -v "$DIR:/backup:ro" "$PGIMAGE" \
    sh -c "find /data -mindepth 1 -delete && tar xzf /backup/$archive -C /data"
}
restore_volume seaweed.tar.gz seaweed
restore_volume n8n.tar.gz n8n
restore_volume clickhouse.tar.gz clickhouse-data

log "restore complete. Next: make up-staged (or make up), then scripts/restore.sh --check $DIR"
