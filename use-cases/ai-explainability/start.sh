#!/bin/bash
set -e

cd "$(dirname "$0")"
docker network create ai-effect-services 2>/dev/null || true
docker compose up -d --build --remove-orphans

echo "Explainability runner: http://localhost:18201/health"
echo "Workflow UI:           http://localhost:18204"