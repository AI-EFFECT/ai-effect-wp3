#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "Stopping the SYSLAB design-of-experiments pipeline..."
docker compose down

echo "Done."
