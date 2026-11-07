# Backup and restore

`scripts/backup.sh` and `scripts/restore.sh` (doc 03 §6.5), wrapped by `make backup`, `make restore` and
`make restore-check`. The procedure was tested end to end in M10 together with the clean-clone installation test
(worklog M10): backup → all volumes wiped → fresh clone installed by the docs → restore → row counts compared.

## What is backed up

| Data | Where it lives | In the backup |
|---|---|---|
| Application data (tenants, employees, leave, documents' metadata and chunks, vault, audit, conversations, checkpoints) | database `leave` | `leave.dump` (`pg_dump -Fc`) |
| Users, roles, sessions | database `keycloak` | `keycloak.dump` |
| n8n workflows, executions, credentials (encrypted) | database `n8n` + volume `n8n` | `n8n.dump`, `n8n.tar.gz` |
| Uploaded files, extracted structures, encrypted supporting documents, Langfuse blobs | volume `seaweed` | `seaweed.tar.gz` |
| Langfuse projects, datasets (with `OBS=1`) | database `langfuse` + volume `clickhouse-data` | `langfuse.dump`, `clickhouse.tar.gz` |
| — | volume `models` | not backed up: re-downloadable with `make models` |
| — | volume `mailpit`, `chroma` | not backed up: captured test mail, evaluation copy of the vectors |
| **Keys** | `deploy/.env` | **not copied — keep it with the backup yourself** |

`manifest.json` records the date, the commit, every file's size and the row counts of the main tables, which the
restore check compares.

## The keys

The dumps contain encrypted data whose keys are only in `deploy/.env`:

| Key | Protects | Without it after a restore |
|---|---|---|
| `APP_KEK` | supporting documents, OAuth refresh tokens | supporting documents cannot be downloaded; calendar connections must be re-authorised |
| `VAULT_KEK` | the originals in the pseudonym vault | names cannot be restored in answers (placeholders stay) |
| `VAULT_HMAC_KEY` | placeholder lookup keys | the same person gets a new placeholder; history and documents no longer match |
| `N8N_ENCRYPTION_KEY` | credentials stored in n8n | n8n cannot read its credentials |

Store a copy of `deploy/.env` next to each backup, protected like the backup itself. Never commit it.

## Back up

```bash
make up-staged                          # PostgreSQL must run
make backup DIR=/path/to/backups/2026-11-07          # core data
make backup DIR=/path/to/backups/2026-11-07 OBS=1    # plus Langfuse (ClickHouse + its database)
cp deploy/.env /path/to/backups/2026-11-07/env       # the keys, kept with the backup (protect this directory)
```

The databases are dumped while the stack runs (`pg_dump` takes a consistent snapshot). SeaweedFS and n8n (and
ClickHouse) are stopped for the seconds their volume is archived, then started again; uploads fail during that time.

## Restore

```bash
make restore DIR=/path/to/backups/2026-11-07     # asks for confirmation; replaces databases and volumes
make up-staged                                   # start everything again
make restore-check DIR=/path/to/backups/2026-11-07   # row counts against the manifest
```

The restore stops every container, starts PostgreSQL alone, drops and recreates each database with its owner from
`deploy/postgres/init/01-init.sh` (the roles must exist: they do on any volume initialised with the same
`deploy/.env`), restores the dumps and refills the volumes. On a fresh machine: clone, put the saved `env` file in
place as `deploy/.env`, `make up-staged` once (initialises the volume and the roles), then restore.

The `models` volume is not restored: run `make models` on a new machine.

## Limits
- Local, file-based backups; no schedule, rotation or off-site copy (out of scope for the local deployment).
- A restore replaces everything; there is no point-in-time or per-tenant restore.
- Keycloak's database holds the realm; a realm re-import from `deploy/keycloak/` is not needed after a restore.
