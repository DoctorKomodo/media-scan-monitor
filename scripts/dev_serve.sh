#!/usr/bin/env bash
# Stand up a local dev instance of media-scan-monitor with sample data, bound to all
# interfaces so it's reachable over the LAN. State lives in ./dev-data (gitignored, throwaway).
#
#   scripts/dev_serve.sh            # http://0.0.0.0:8099, password "dev"
#   MSM_PORT=8080 MSM_PASSWORD=hunter2 scripts/dev_serve.sh
#
# Requires `uv` (https://docs.astral.sh/uv/). Ctrl-C to stop.
set -euo pipefail
cd "$(dirname "$0")/.."

export MSM_DB_PATH="${MSM_DB_PATH:-dev-data/app.db}"
export MSM_SECRET_KEY_FILE="${MSM_SECRET_KEY_FILE:-dev-data/secret.key}"
export MSM_HOST="${MSM_HOST:-0.0.0.0}"
export MSM_PORT="${MSM_PORT:-8099}"
export MSM_PASSWORD="${MSM_PASSWORD:-dev}"

echo "==> Syncing dependencies (uv, incl. dev tools)"
uv sync --extra dev --quiet

echo "==> Seeding dev database ($MSM_DB_PATH)"
uv run python scripts/dev_seed.py

lan_ip=$(ip -4 -o addr show scope global 2>/dev/null | awk 'NR==1{sub(/\/.*/,"",$4); print $4}')
echo
echo "==> Serving on http://${MSM_HOST}:${MSM_PORT}  (password: ${MSM_PASSWORD})"
[ -n "${lan_ip:-}" ] && echo "    LAN:  http://${lan_ip}:${MSM_PORT}"
echo
exec uv run msm run
