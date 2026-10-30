#!/bin/bash
set -e

cd "$(dirname "$0")"

docker network create ai-effect-services 2>/dev/null || true

mkdir -p output

echo "Building and starting the SYSLAB design-of-experiments pipeline (design -> synthetic runner -> KPIs -> analysis)..."
docker compose up --build -d --remove-orphans

echo ""
echo "Services:"
echo "  design-of-experiments: http://localhost:18301"
echo "  synthetic-runner:      http://localhost:18302"
echo "  doe-analysis:          http://localhost:18303"
echo "  kpi-calculator:        http://localhost:18304"
echo ""
echo "Check logs: docker compose logs -f"
