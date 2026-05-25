#!/usr/bin/env bash
# AlphaForge launcher (Mac/Linux)
set -e

cd "$(dirname "$0")"

# Copy env templates if missing
[ -f backend/.env ] || cp backend/.env.example backend/.env
[ -f frontend/.env ] || cp frontend/.env.example frontend/.env

echo "▶ Building & starting AlphaForge (Docker Compose)…"
docker compose up -d --build

echo "⏳ Waiting for services to come up…"
for i in {1..30}; do
  if curl -fsS http://localhost:8001/api/health >/dev/null 2>&1; then
    echo "✓ Backend ready"
    break
  fi
  sleep 2
done

echo "✓ Frontend:  http://localhost:3000"
echo "✓ Backend:   http://localhost:8001/api/health"

# Open browser on Mac
if [[ "$OSTYPE" == "darwin"* ]]; then
  open http://localhost:3000 || true
# Linux
elif command -v xdg-open &> /dev/null; then
  xdg-open http://localhost:3000 || true
fi

echo ""
echo "Stop with:   docker compose stop"
echo "Logs with:   docker compose logs -f backend"
