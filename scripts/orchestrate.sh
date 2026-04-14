#!/usr/bin/env bash
# SDMNH Orchestrator — runs on the existing DO droplet.
# Creates an ephemeral renderer droplet, runs the pipeline, destroys it.
set -euo pipefail

SDMNH_DIR="/opt/sdmnh"
LOG="$SDMNH_DIR/data/orchestrator.log"
REGION="${SDMNH_REGION:-lon1}"
SIZE="${SDMNH_SIZE:-s-2vcpu-4gb}"
IMAGE="ubuntu-24-04-x64"
SSH_KEY_FINGERPRINT="${SDMNH_SSH_KEY_FINGERPRINT:?Set SDMNH_SSH_KEY_FINGERPRINT}"
RENDERER_NAME="sdmnh-renderer-$(date +%Y%m%d-%H%M%S)"
DROPLET_ID=""

log() { echo "$(date -Iseconds) $*" | tee -a "$LOG"; }

cleanup() {
    if [[ -n "$DROPLET_ID" ]]; then
        log "Destroying renderer droplet $DROPLET_ID..."
        doctl compute droplet delete "$DROPLET_ID" --force 2>/dev/null || true
        log "Renderer destroyed."
    fi
}
trap cleanup EXIT

# ── 1. Refresh database locally (lightweight) ──
log "=== SDMNH Orchestrator starting ==="
cd "$SDMNH_DIR"
source .venv/bin/activate
python update_db.py 2>&1 | tee -a "$LOG"

# ── 2. Create ephemeral renderer ──
log "Creating renderer droplet ($SIZE in $REGION)..."
DROPLET_ID=$(doctl compute droplet create "$RENDERER_NAME" \
    --region "$REGION" \
    --size "$SIZE" \
    --image "$IMAGE" \
    --ssh-keys "$SSH_KEY_FINGERPRINT" \
    --user-data-file "$SDMNH_DIR/scripts/renderer-init.sh" \
    --wait \
    --format ID \
    --no-header)

DROPLET_IP=$(doctl compute droplet get "$DROPLET_ID" --format PublicIPv4 --no-header)
log "Renderer created: $DROPLET_ID @ $DROPLET_IP"

# ── 3. Wait for SSH to be ready ──
log "Waiting for SSH on $DROPLET_IP..."
for i in $(seq 1 30); do
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes \
        root@"$DROPLET_IP" true 2>/dev/null; then
        break
    fi
    sleep 10
done

# Wait for cloud-init to finish installing dependencies
ssh -o StrictHostKeyChecking=no root@"$DROPLET_IP" \
    "cloud-init status --wait" 2>&1 | tee -a "$LOG"

# ── 4. Transfer files to renderer ──
log "Transferring project files to renderer..."
ssh -o StrictHostKeyChecking=no root@"$DROPLET_IP" "mkdir -p /opt/sdmnh"

# Sync project (exclude heavy/local-only dirs)
rsync -az --exclude '.git' \
    --exclude 'data/downloads' \
    --exclude 'data/outputs' \
    --exclude '__pycache__' \
    --exclude '.venv' \
    --exclude 'tests' \
    "$SDMNH_DIR/" root@"$DROPLET_IP":/opt/sdmnh/

log "Files transferred."

# ── 5. Run pipeline on renderer ──
log "Starting render pipeline..."
ssh -o StrictHostKeyChecking=no root@"$DROPLET_IP" \
    "bash /opt/sdmnh/scripts/render-pipeline.sh" 2>&1 | tee -a "$LOG"
PIPELINE_EXIT=$?

# ── 6. Retrieve updated files ──
log "Retrieving updated files from renderer..."
scp -o StrictHostKeyChecking=no \
    root@"$DROPLET_IP":/opt/sdmnh/data/videos.db \
    "$SDMNH_DIR/data/videos.db" 2>/dev/null || true

scp -o StrictHostKeyChecking=no \
    root@"$DROPLET_IP":/opt/sdmnh/credentials/youtube_token.json \
    "$SDMNH_DIR/credentials/youtube_token.json" 2>/dev/null || true

scp -o StrictHostKeyChecking=no \
    root@"$DROPLET_IP":/opt/sdmnh/data/last_run.json \
    "$SDMNH_DIR/data/last_run.json" 2>/dev/null || true

# ── 7. Droplet destruction happens in trap ──
if [[ "$PIPELINE_EXIT" -eq 0 ]]; then
    log "Pipeline completed successfully."
else
    log "Pipeline failed with exit code $PIPELINE_EXIT."
fi

log "=== Orchestrator finished ==="
