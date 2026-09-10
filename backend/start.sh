#!/usr/bin/env bash
# Render start command. warehouse/volterra.duckdb isn't committed to git (warehouse/*.duckdb is
# gitignored -- it's a build artifact, not source) and Render's free-tier disk isn't guaranteed
# to survive a redeploy, so this fetches it from a GitHub Release asset on every boot unless it's
# already present. Same pattern as AUTOMOTIVE_PRODUCT_V1/backend/start.sh.
set -euo pipefail

cd "$(dirname "$0")"

WAREHOUSE_PATH="../warehouse/volterra.duckdb"

if [ ! -f "$WAREHOUSE_PATH" ]; then
  if [ -z "${VOLTERRA_WAREHOUSE_URL:-}" ]; then
    echo "VOLTERRA_WAREHOUSE_URL is not set and $WAREHOUSE_PATH is missing -- /api/sites and /api/demand will 500." >&2
  else
    echo "Downloading warehouse from \$VOLTERRA_WAREHOUSE_URL..."
    mkdir -p "$(dirname "$WAREHOUSE_PATH")"
    curl -fL "$VOLTERRA_WAREHOUSE_URL" -o "$WAREHOUSE_PATH"
  fi
fi

exec ./bin/api
