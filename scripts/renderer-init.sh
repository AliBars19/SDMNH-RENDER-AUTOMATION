#!/usr/bin/env bash
# Cloud-init user-data script for the ephemeral renderer droplet.
# Installs system dependencies needed by the render pipeline.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv ffmpeg rsync

# Install yt-dlp (latest from GitHub releases for best compatibility)
curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp
chmod a+rx /usr/local/bin/yt-dlp

echo "renderer-init complete" > /tmp/renderer-ready
