#!/usr/bin/env bash
# Scan staged (or all tracked) files for likely API keys before commit/push.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if git rev-parse --git-dir >/dev/null 2>&1; then
  FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)
  if [[ -z "${FILES// }" ]]; then
    FILES=$(git ls-files 2>/dev/null || true)
  fi
else
  echo "check-secrets: not a git repo" >&2
  exit 1
fi

[[ -z "${FILES// }" ]] && { echo "check-secrets: nothing to scan"; exit 0; }

PATTERNS=(
  'ghp_[0-9a-zA-Z]{20,}'
  'github_pat_[0-9a-zA-Z_]{20,}'
  'gho_[0-9a-zA-Z]{20,}'
  'sk-ant-api[0-9a-zA-Z_-]{20,}'
  'xox[baprs]-[0-9a-zA-Z-]{10,}'
)

FOUND=0
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  [[ ! -f "$f" ]] && continue
  case "$f" in
    .env|.env.*|*.pem|*.key) continue ;;
    .env.example|e2e/helpers/navigation.ts|scripts/check-secrets.sh) continue ;;
  esac
  for pat in "${PATTERNS[@]}"; do
    if grep -qE "$pat" "$f" 2>/dev/null; then
      echo "check-secrets: possible secret in $f (pattern: $pat)" >&2
      FOUND=1
    fi
  done
done <<< "$FILES"

if [[ "$FOUND" -ne 0 ]]; then
  echo "check-secrets: FAILED — remove secrets before committing" >&2
  exit 1
fi
echo "check-secrets: OK"
