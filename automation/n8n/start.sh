#!/bin/sh
# n8n service start (compose profile `automation`): the owner account is managed by the environment
# (N8N_INSTANCE_OWNER_*); its bcrypt hash is derived here from N8N_OWNER_PASSWORD so no hash is kept in .env.
set -eu
if [ -z "${N8N_OWNER_PASSWORD:-}" ]; then
  echo "N8N_OWNER_PASSWORD is empty: run make env" >&2
  exit 2
fi
N8N_INSTANCE_OWNER_PASSWORD_HASH=$(node -e '
const bcrypt = require("/usr/local/lib/node_modules/n8n/node_modules/bcryptjs");
process.stdout.write(bcrypt.hashSync(process.env.N8N_OWNER_PASSWORD, 10));
')
export N8N_INSTANCE_OWNER_PASSWORD_HASH
exec n8n start
