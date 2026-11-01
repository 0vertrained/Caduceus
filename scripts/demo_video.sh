#!/usr/bin/env bash
# Record the captioned demo walkthrough and join it into one MP4 for the site (ui/public/demo.mp4).
#   scripts/demo_video.sh [out.mp4]
# Needs: the Python package installed, ui/dist built, Node with Playwright and Chromium, ffmpeg, Tesseract.
# Drafts come from data/recorded/completions.json when it exists, otherwise from the template.
set -euo pipefail
cd "$(dirname "$0")/.."

out="${1:-ui/public/demo.mp4}"
port="${VIDEO_PORT:-8766}"
work="$(mktemp -d)"
trap 'kill "${server:-0}" 2>/dev/null || true; rm -rf "$work"' EXIT

export CADUCEUS_AUTH_MODE=demo CADUCEUS_DB="$work/video.sqlite3"
# The showcase cases, and a payer that takes a minute to decide (half when expedited).
export CADUCEUS_SHOWCASE=1 CADUCEUS_PAYER_REVIEW_SECONDS="${CADUCEUS_PAYER_REVIEW_SECONDS:-60}"
uvicorn caduceus.api:create_app --factory --host 127.0.0.1 --port "$port" --log-level warning &
server=$!
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null 2>&1 && break
  sleep 0.25
done

NODE_PATH="${NODE_PATH:-$(npm root -g)}" node ui/e2e/video.mjs "http://127.0.0.1:$port" "$work/clips"

# Each clip becomes an H.264 segment; the segments are joined without re-encoding.
i=0
: > "$work/list.txt"
while IFS= read -r clip; do
  i=$((i + 1))
  ffmpeg -nostdin -loglevel error -y -i "$clip" -c:v libx264 -preset veryfast -crf 26 -pix_fmt yuv420p -r 30 -an \
    "$work/part$i.mp4"
  echo "file '$work/part$i.mp4'" >> "$work/list.txt"
done < <(ls -tr "$work"/clips/*.webm)
mkdir -p "$(dirname "$out")"
ffmpeg -loglevel error -y -f concat -safe 0 -i "$work/list.txt" -c copy -movflags +faststart "$out"
echo "Wrote $out ($(du -h "$out" | cut -f1))"
