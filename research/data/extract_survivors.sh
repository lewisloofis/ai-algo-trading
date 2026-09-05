#!/usr/bin/env bash
# Extract per-symbol, per-day features + trades CSVs from captured segments.
#
# Output layout matches fill_calibration._discover_pairs (subdir branch):
#   <OUT>/<SYMBOL>/<date>_features.csv
#   <OUT>/<SYMBOL>/<date>_trades.csv
# where <date> is the segment partition (e.g. 2026-06-26). All segments of a day
# are concatenated into that day's CSV (single header); days are kept as separate
# files so the harness counts trading days correctly.
#
# Spans watchlist_id, on purpose (roadmap W0.2): a capture-config change (e.g.
# infra/terraform.tfvars widening the watchlist) forks the on-disk directory
# name (segments/dt=<date>/<watchlist_id>/), not the date. Filtering by a
# specific watchlist_id here would silently truncate every symbol's history
# to whichever id happened to be active on a given day. Instead, every
# watchlist_id directory under a date is read and combined into that one
# date's CSV (see the DAY_DIRS/SEGS discovery below). Segments are
# ordered by their filename's embedded capture time (seg-HHMMSS...), not by
# directory name, because `sbcap features` carries rolling cross-segment
# book state (delta-volume, last book side) that must be fed in
# chronological order; sorting by watchlist_id first would interleave two
# ids' segments out of time order on any date that captured under both (a
# same-day cutover).
#
# Usage: extract_survivors.sh [SEGMENTS_ROOT] [OUT_DIR]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SEGMENTS_ROOT="${1:-$REPO_ROOT/segments}"
OUT_DIR="${2:-$REPO_ROOT/research/data/survivors}"
SBCAP="${SBCAP:-/tmp/sbcap}"
# The plan-10 survivors. Widening this list (roadmap W0.2 proposes adding the
# screener top-N plus a random control slice) is independent of the
# multi-watchlist_id reading fixed here — extend this array once that
# capture lands.
SYMBOLS=(PTRO TPIA BRPT ENRG RAJA BREN RATU)

if [[ ! -x "$SBCAP" ]]; then
  echo "sbcap binary not found at $SBCAP; build with: (cd collector && go build -o /tmp/sbcap ./cmd/sbcap)" >&2
  exit 1
fi

# Every dt=YYYY-MM-DD day directory under SEGMENTS_ROOT, regardless of which
# watchlist_id(s) it holds.
DAY_DIRS=()
while IFS= read -r d; do
  DAY_DIRS+=("$d")
done < <(find "$SEGMENTS_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'dt=*' | sort)

if [[ ${#DAY_DIRS[@]} -eq 0 ]]; then
  echo "No dt=* day partitions found under $SEGMENTS_ROOT" >&2
  exit 1
fi
echo "Found ${#DAY_DIRS[@]} day partitions under $SEGMENTS_ROOT." >&2

for day_dir in "${DAY_DIRS[@]}"; do
  # Dir name is "dt=YYYY-MM-DD"; strip the dt= prefix for the date label.
  parent="$(basename "$day_dir")"
  date="${parent#dt=}"

  # Segment files across every watchlist_id subdirectory for this date,
  # sorted by basename (embeds capture start time) so a same-day watchlist
  # cutover still replays in chronological order — see header comment.
  # (awk prefixes each path with its basename as a sort key, then cut drops
  # the key back off; this never depends on GNU-only find/sort extensions.)
  SEGS=()
  while IFS= read -r f; do
    SEGS+=("$f")
  done < <(find "$day_dir" -mindepth 2 -maxdepth 2 -name '*.binpb.zst' \
             | awk -F/ '{print $NF"\t"$0}' | sort -k1,1 | cut -f2-)
  [[ ${#SEGS[@]} -eq 0 ]] && continue

  for sym in "${SYMBOLS[@]}"; do
    sym_dir="$OUT_DIR/$sym"
    mkdir -p "$sym_dir"
    feat_out="$sym_dir/${date}_features.csv"
    trade_out="$sym_dir/${date}_trades.csv"

    "$SBCAP" features "${SEGS[@]}" --symbol "$sym" > "$feat_out"
    "$SBCAP" trades "${SEGS[@]}" --symbol "$sym" > "$trade_out"

    feat_rows=$(($(wc -l < "$feat_out") - 1))
    trade_rows=$(($(wc -l < "$trade_out") - 1))
    echo "[$date $sym] features=$feat_rows trades=$trade_rows" >&2
  done
done

echo "Done. CSVs written under $OUT_DIR/<SYMBOL>/<date>_{features,trades}.csv" >&2
