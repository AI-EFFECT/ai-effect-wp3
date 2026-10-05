#!/bin/bash
set -e

cd "$(dirname "$0")"
WP3_REPO="${WP3_REPO:-../..}"
GENERATOR="$WP3_REPO/scripts/onboarding-export-generator.py"

if [ ! -f "$GENERATOR" ]; then
  echo "Generator not found: $GENERATOR" >&2
  exit 1
fi

python3 "$GENERATOR" "$(pwd)" export --overwrite --zip