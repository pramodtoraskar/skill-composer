#!/usr/bin/env bash
# Run Playwright E2E against Skills Composer (default http://localhost:3747).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
E2E="$ROOT/e2e"

if [[ ! -d "$E2E/node_modules/@playwright/test" ]]; then
  echo "Installing Playwright deps in e2e/ …"
  (cd "$E2E" && npm install && npx playwright install chromium)
fi

export PLAYWRIGHT_BASE_URL="${PLAYWRIGHT_BASE_URL:-http://localhost:3747}"
# Reuse server if you already run: bash serve-app.sh --port 3747 --no-browser
export PLAYWRIGHT_SKIP_WEBSERVER="${PLAYWRIGHT_SKIP_WEBSERVER:-}"

cd "$E2E"
if [[ -z "$PLAYWRIGHT_SKIP_WEBSERVER" ]]; then
  echo "Playwright will start serve-app.sh on 3747 if not already running."
else
  echo "Expecting app at $PLAYWRIGHT_BASE_URL (PLAYWRIGHT_SKIP_WEBSERVER=1)"
fi

npm test
