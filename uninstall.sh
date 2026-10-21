#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$EUID" -eq 0 ]]; then SUDO=(); else SUDO=(sudo); fi
read -r -p 'Stop and remove xsukax RSS containers (data and .env remain)? Type YES: ' confirm
[[ "$confirm" == YES ]] || { echo 'Canceled'; exit 0; }
"${SUDO[@]}" docker compose down --remove-orphans
echo 'Application stopped. Data in ./data and secrets in .env have NOT been deleted.'
