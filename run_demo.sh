#!/usr/bin/env bash
# Start everything for the demo with one command:
#   - demo look-alike pages on http://localhost:8001 (localhost only)
#   - the web app on http://127.0.0.1:8000
# Press Ctrl+C to stop both.

cd "$(dirname "$0")"
PY=.venv/bin/python

# Free the two ports if an earlier run is still going
for port in 8000 8001; do
  lsof -ti tcp:$port | xargs kill 2>/dev/null
done

backend=$(grep -E '^VISION_BACKEND=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ')
model=$(grep -E '^OLLAMA_MODEL=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ')
if [ "$backend" = "ollama" ]; then
  if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null; then
    echo "Ollama is not running. Open the Ollama app, then run this again."
    exit 1
  fi
  # Load Gemma 4 into memory now, so the first check in the demo is fast
  echo "Loading ${model:-gemma4:e4b} into memory..."
  curl -s http://127.0.0.1:11434/api/generate \
    -d "{\"model\": \"${model:-gemma4:e4b}\", \"keep_alive\": \"30m\"}" >/dev/null
fi

$PY -m http.server 8001 --bind 127.0.0.1 --directory demo_sites >/dev/null 2>&1 &
DEMO_PID=$!
trap 'kill $DEMO_PID 2>/dev/null' EXIT

echo ""
echo "  App:        http://127.0.0.1:8000"
echo "  Demo pages: http://localhost:8001"
echo "  AI backend: ${backend:-gemini}"
echo "  Press Ctrl+C to stop."
echo ""
$PY -m detector.server
