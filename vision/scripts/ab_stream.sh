#!/bin/bash
# One arm of the detection A/B: 3 LSV clips x 2 repeats, saved under runs/ab/.
#   scripts/ab_stream.sh off   # no detection
#   scripts/ab_stream.sh on    # --detect (needs the detection extras)
# Costs real money (about $2.2 per pass over the three clips with detection off). The two arms
# can run in parallel. Score with scripts/ab_score.py.
set -u
cd "$(dirname "$0")/.."
MODE=${1:?usage: ab_stream.sh off|on}
F=fixtures/lsv_mock_transformation
EXTRA="--dump-frames"; [ "$MODE" = "on" ] && EXTRA="--detect --dump-frames"
for rep in 1 2; do
  for clip in DJI-091_issue_skipped_step3 DJI-092_correct_rep1 DJI-093_correct_rep2; do
    id=${clip%%_*}; out=runs/ab/$id-$MODE-r$rep
    echo "=== $(date +%T) start $out"
    python3 -m lab_vision.cli run --protocol $F/protocol.yaml --video "data/videos/$clip.mp4" \
      --out "$out" $EXTRA 2>&1 | grep -v -i "warn\|slow image\|HTTP Request\|NumExpr" \
      | grep -E "windows|usage|deviations"
    echo "=== $(date +%T) done  $out"
  done
done
echo "STREAM $MODE COMPLETE"
