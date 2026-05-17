#!/usr/bin/env bash
# Record README demo video with Playwright (mocked APIs) → docs/demo.webm
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
E2E="$ROOT/e2e"
DOCS="$ROOT/docs"
OUT_WEBM="$DOCS/demo.webm"
OUT_MP4="$DOCS/demo.mp4"

mkdir -p "$DOCS"

if [[ ! -d "$E2E/node_modules/@playwright/test" ]]; then
  echo "Installing Playwright deps…"
  (cd "$E2E" && npm install && npx playwright install chromium)
fi

export PLAYWRIGHT_BASE_URL="${PLAYWRIGHT_BASE_URL:-http://localhost:3747}"

echo "Recording demo (mocked LLM + GitHub)…"
rm -rf "$E2E/demo-output"
set +e
(cd "$E2E" && npx playwright test --config=playwright.demo.config.ts)
PW_EXIT=$?
set -e

VIDEO="$(find "$E2E/demo-output" -name 'video.webm' -type f 2>/dev/null | head -1)"
if [[ -z "$VIDEO" || ! -f "$VIDEO" ]]; then
  echo "No video.webm found under e2e/demo-output" >&2
  exit 1
fi

cp "$VIDEO" "$OUT_WEBM"
echo "Wrote $OUT_WEBM ($(du -h "$OUT_WEBM" | cut -f1))"

OUT_GIF="$DOCS/demo.gif"

if command -v ffmpeg >/dev/null 2>&1; then
  ffmpeg -y -i "$OUT_WEBM" -an -vf "scale=1280:-2" -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$OUT_MP4" 2>/dev/null \
    && echo "Wrote $OUT_MP4 ($(du -h "$OUT_MP4" | cut -f1))"
  # Sharp GIF: full 1280px capture, 256 colors, no dither (keeps UI text crisp)
  ffmpeg -y -i "$OUT_WEBM" -vf "fps=12,scale=1280:-2:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff:max_colors=256[p];[s1][p]paletteuse=dither=none" -loop 0 "$OUT_GIF" 2>/dev/null \
    && echo "Wrote $OUT_GIF ($(du -h "$OUT_GIF" | cut -f1))"
else
  echo "Tip: install ffmpeg for docs/demo.mp4 and docs/demo.gif (README uses .gif)."
fi

[[ "$PW_EXIT" -ne 0 ]] && echo "Warning: Playwright exited $PW_EXIT (video may still be usable)" >&2
echo "Done. README embeds docs/demo.gif"
exit 0
