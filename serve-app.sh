#!/usr/bin/env bash
# Start Skills Composer (local server.py + index.html in this folder).
#
# Usage:
#   bash serve-app.sh
#   cp .env.example .env   # edit ANTHROPIC_API_KEY
#   bash serve-app.sh --port 3000 --no-browser
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Creating .venv …"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip -q
  "$VENV/bin/pip" install -r "$ROOT/requirements.txt" -q
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

if [[ ! -f "$ROOT/index.html" ]]; then
  if [[ -f "$ROOT/updates/index.html" ]]; then
    echo "Note: index.html missing — server will use updates/index.html (or restore: cp updates/index.html index.html)"
  else
    echo "Warning: no index.html under $ROOT — GET / will 404 until you add one." >&2
  fi
fi

exec "$VENV/bin/python" "$ROOT/server.py" "$@"
