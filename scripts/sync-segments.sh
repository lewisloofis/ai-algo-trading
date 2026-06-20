#!/usr/bin/env bash
set -euo pipefail

BUCKET="s3://marketdata-047915756052"
AWS_PROFILE="lewis"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEGMENTS_DIR="$SCRIPT_DIR/../segments"
TODAY=$(date +%Y-%m-%d)
LOG_DIR="$SCRIPT_DIR/../logs"
LOG_FILE="$LOG_DIR/sync-segments-$TODAY.log"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "Syncing segments for $TODAY"

aws s3 sync \
  "$BUCKET/raw/dt=$TODAY/" \
  "$SEGMENTS_DIR/dt=$TODAY/" \
  --profile "$AWS_PROFILE" \
  2>&1 | tee -a "$LOG_FILE"

log "Done. Local path: $SEGMENTS_DIR/dt=$TODAY/"
