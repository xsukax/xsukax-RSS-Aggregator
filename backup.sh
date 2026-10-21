#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo 'Missing .env' >&2; exit 1; }
if [[ "$EUID" -eq 0 ]]; then SUDO=(); else SUDO=(sudo); fi
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p backups
# sqlite3 online backup API ensures a transactionally consistent snapshot while app runs.
"${SUDO[@]}" docker compose exec -T app python - "$STAMP" <<'PY'
import os,sqlite3,sys
from pathlib import Path
stamp=sys.argv[1];dst=Path('/data/rss-backup-'+stamp+'.sqlite3')
source=sqlite3.connect('/data/rss.sqlite3');target=sqlite3.connect(dst)
source.backup(target);target.close();source.close()
print('Database snapshot:',dst)
PY
"${SUDO[@]}" cp "data/rss-backup-$STAMP.sqlite3" "backups/rss-$STAMP.sqlite3"
"${SUDO[@]}" rm -f "data/rss-backup-$STAMP.sqlite3"
# Snapshot includes credentials and feed content: protect and store off-server.
"${SUDO[@]}" tar -czf "backups/xsukax-rss-$STAMP.tar.gz" .env -C backups "rss-$STAMP.sqlite3"
"${SUDO[@]}" chmod 600 "backups/xsukax-rss-$STAMP.tar.gz"
"${SUDO[@]}" rm -f "backups/rss-$STAMP.sqlite3"
printf 'Backup created: %s\n' "$(pwd)/backups/xsukax-rss-$STAMP.tar.gz"
