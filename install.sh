#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

say(){ printf '\033[1;34m%s\033[0m\n' "$*"; }
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f /etc/os-release ]] || fail 'This installer requires a Debian-based Linux host.'
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
  debian|ubuntu) ;;
  *) [[ "${ID_LIKE:-}" == *debian* ]] || fail 'Supported installer hosts: Debian or Ubuntu. Use Docker Compose manually elsewhere.';;
esac

if [[ "${EUID}" -eq 0 ]]; then SUDO=(); else
  command -v sudo >/dev/null || fail 'Run as root or install sudo.'
  SUDO=(sudo)
fi

install_docker(){
  say 'Installing Docker Engine and Docker Compose from the official Docker repository…'
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y ca-certificates curl gnupg
  "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
  "${SUDO[@]}" curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  "${SUDO[@]}" chmod a+r /etc/apt/keyrings/docker.asc
  local arch codename
  arch="$(dpkg --print-architecture)";codename="${VERSION_CODENAME:-}"
  [[ -n "$codename" ]] || fail 'Cannot determine Debian/Ubuntu codename'
  printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$ID" "$codename" "$arch" | "${SUDO[@]}" tee /etc/apt/sources.list.d/docker.sources >/dev/null
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  "${SUDO[@]}" systemctl enable --now docker
}

if ! command -v docker >/dev/null || ! "${SUDO[@]}" docker compose version >/dev/null 2>&1; then install_docker; fi
"${SUDO[@]}" docker info >/dev/null || fail 'Docker daemon is not running'

if [[ ! -f .env ]]; then
  PORT="${APP_PORT:-8090}"
  [[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT>=1024 && PORT<=65535 )) || fail 'APP_PORT must be a valid port between 1024 and 65535'
  if command -v openssl >/dev/null; then
    SECRET="$(openssl rand -hex 32)";TOKEN="$(openssl rand -hex 24)"
  else
    command -v python3 >/dev/null || "${SUDO[@]}" apt-get install -y python3
    SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
    TOKEN="$(python3 -c 'import secrets;print(secrets.token_hex(24))')"
  fi
  umask 077
  cat > .env <<ENV
APP_PORT=$PORT
REFRESH_WORKERS=2
PUBLIC_URL=
APP_SECRET=$SECRET
SETUP_TOKEN=$TOKEN
ENV
  say 'Created .env with new random application and first-run secrets.'
else
  say 'Existing .env preserved (no credentials overwritten).'
fi
chmod 600 .env
# The container runs as non-root uid/gid 10001.
"${SUDO[@]}" install -d -m 750 -o 10001 -g 10001 data
"${SUDO[@]}" chown 10001:10001 data
say 'Building and starting the application…'
"${SUDO[@]}" docker compose up -d --build

# `up -d` only confirms that Docker started the container, not that the app booted.
# Check Docker's application health status before claiming successful installation.
container_id="$("${SUDO[@]}" docker compose ps -a -q app)"
[[ -n "$container_id" ]] || fail 'Application container was not created.'
healthy=0
for _ in {1..45}; do
  status="$("${SUDO[@]}" docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
  if [[ "$status" == 'healthy' ]]; then healthy=1; break; fi
  if [[ "$status" == 'unhealthy' || "$status" == 'exited' || "$status" == 'dead' || "$status" == 'restarting' ]]; then
    break
  fi
  sleep 2
done
if (( ! healthy )); then
  printf '\nThe application did not become healthy (status: %s). Recent container logs:\n' "${status:-unknown}" >&2
  "${SUDO[@]}" docker compose logs --tail=100 app >&2 || true
  fail 'Container startup failed. Your .env and data/ were preserved.'
fi

PORT="$(sed -n 's/^APP_PORT=//p' .env | head -n 1)"
say "xsukax RSS Aggregator is healthy: http://127.0.0.1:${PORT}"
say 'On first visit, create your own administrator username and password in the web interface.'
say 'First-run setup token (keep private; needed only until administrator is created):'
sed -n 's/^SETUP_TOKEN=//p' .env
printf '\nCloudflare Tunnel origin example: http://localhost:%s\n' "$PORT"
printf 'Useful commands: docker compose logs -f | docker compose ps | docker compose restart\n'
printf 'Backup: ./backup.sh\n'
