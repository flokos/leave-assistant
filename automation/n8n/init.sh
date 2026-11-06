#!/bin/sh
# n8n-init (compose profile `automation`, doc 06 §6): render the credentials from the environment, import them and
# the workflows (fixed ids → re-runs overwrite), then publish every workflow (n8n 2.x replaced activation with
# publishing; one id at a time, effective at the next start — the n8n service starts after this job).
set -eu
DIR=/automation
RENDERED=/tmp/credentials.json
node -e '
const fs = require("fs");
const text = fs.readFileSync(process.argv[1], "utf8").replace(/\$\{([A-Z0-9_]+)\}/g, (_, k) => {
  if (!process.env[k]) { console.error("missing " + k); process.exit(2); }
  return process.env[k];
});
fs.writeFileSync(process.argv[2], text, { mode: 0o600 });
' "$DIR/credentials.template.json" "$RENDERED"
n8n import:credentials --input="$RENDERED"
: > "$RENDERED"   # the plain-text copy is emptied at once (the container's /tmp is discarded with it)
n8n import:workflow --separate --input="$DIR/workflows"
for file in "$DIR"/workflows/*.json; do
  id=$(node -e 'process.stdout.write(require(process.argv[1]).id)' "$file")
  n8n publish:workflow --id="$id"
done
echo "n8n-init: credentials and $(ls "$DIR"/workflows/*.json | wc -l) workflows imported and published"
