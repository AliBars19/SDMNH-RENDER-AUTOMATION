#!/usr/bin/env bash
# Runs on the ephemeral renderer droplet.
# Sets up venv, installs Python deps, runs the automation pipeline.
set -euo pipefail

SDMNH_DIR="/opt/sdmnh"

# Safety net: hard kill after 6 hours to prevent orphaned billing
(sleep 21600 && poweroff) &
SAFETY_PID=$!

cd "$SDMNH_DIR"

# Create venv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt

# Run the pipeline (--force skips "already ran today" on fresh droplet,
# --ephemeral skips wait_and_delete_when_public since droplet destruction
# handles file cleanup)
python automation.py --force --ephemeral
EXIT_CODE=$?

# Cancel safety timer
kill "$SAFETY_PID" 2>/dev/null || true

exit "$EXIT_CODE"
