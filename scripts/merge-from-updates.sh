#!/usr/bin/env bash
# Compare updates/ staging to live files and verify preserved features.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UPDATES="$ROOT/updates"
CHECK_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --check-only) CHECK_ONLY=1 ;;
    -h|--help)
      echo "Usage: bash scripts/merge-from-updates.sh [--check-only]"
      echo "  Default: show diff updates/ vs live, then run guard checks on live files."
      echo "  --check-only: skip diff, only verify live files still have required markers."
      exit 0
      ;;
  esac
done

INDEX_MARKERS=(
  "HUB_SKILL_CATEGORIES"
  "f-skill-category"
  "skillMdPath"
  "onSkillCategoryChange"
  "parseSkillPathFromRepo"
  "panel-4"
  "testSkill"
)

SERVER_MARKERS=(
  "normalize_skill_kind"
  "/api/config"
)

fail=0

check_markers() {
  local file="$1"
  shift
  local label="$1"
  shift
  if [[ ! -f "$file" ]]; then
    echo "MISSING: $label ($file)"
    fail=1
    return
  fi
  for m in "$@"; do
    if ! grep -q "$m" "$file"; then
      echo "MISSING marker in $label: $m"
      fail=1
    fi
  done
}

if [[ "$CHECK_ONLY" -eq 0 ]]; then
  echo "=== Diff: updates/index.html → index.html (live minus staging) ==="
  if [[ -f "$UPDATES/index.html" ]]; then
    diff -u "$UPDATES/index.html" "$ROOT/index.html" | head -200 || true
    echo "... (truncated; run: diff -u updates/index.html index.html | less)"
  else
    echo "(no updates/index.html)"
  fi
  echo ""
  echo "=== Diff: updates/server.py → server.py ==="
  if [[ -f "$UPDATES/server.py" ]]; then
    diff -u "$UPDATES/server.py" "$ROOT/server.py" | head -80 || true
    echo "... (truncated; run: diff -u updates/server.py server.py | less)"
  else
    echo "(no updates/server.py)"
  fi
  echo ""
fi

echo "=== Guard check on live files ==="
check_markers "$ROOT/index.html" "index.html" "${INDEX_MARKERS[@]}"
check_markers "$ROOT/server.py" "server.py" "${SERVER_MARKERS[@]}"

if [[ "$fail" -eq 0 ]]; then
  echo "OK: all required markers present in live files."
  exit 0
fi
echo ""
echo "Fix live files before deploying. Do not cp updates/* over root without merging."
exit 1
