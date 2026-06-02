#!/usr/bin/env bash
# Helper for the autoresearch loop.
#   Usage: bash run_exp.sh "<iter-label>" "<description>"
# Runs generate+eval on val, writes a timestamped log, prints key metrics, and
# archives a snapshot of the pipeline + results into attempts/ so progress is
# never lost.
set -e
LABEL="${1:-run}"
DESC="${2:-}"
TS=$(date +%Y%m%d-%H%M%S)
echo "$TS" > /tmp/cur_ts.txt
{ uv run generate.py --out "results/$TS" --split val && uv run eval.py "results/$TS"; } > "logs/$TS.log" 2>&1
echo "TS=$TS"

L="logs/$TS.log"
COMMIT=$(git rev-parse --short HEAD)
SENT=$(grep -oP '^sentence_coverage:\s+\K[0-9.]+' "$L" || echo "")
VAR=$(grep -oP '^variant_coverage:\s+\K[0-9.]+' "$L" || echo "")

# Archive snapshot into attempts/
DIR="attempts/${LABEL}_${COMMIT}"
mkdir -p "$DIR"
cp annotation_pipeline.py "$DIR/annotation_pipeline.py"
{
  echo "iteration: $LABEL"
  echo "commit: $COMMIT"
  echo "timestamp: $TS"
  echo "sentence_coverage: ${SENT:-CRASH}"
  echo "variant_coverage: ${VAR:-CRASH}"
  echo "description: $DESC"
  echo "--- eval summary ---"
  grep -E "^sentence_coverage:|^variant_coverage:|^sentence_precision:|^num_papers:|^num_pred_sentences:|^num_gold_sentences:|^total_seconds:" "$L" || true
} > "$DIR/results.txt"

if [ -n "$SENT" ]; then
  grep -E "^sentence_coverage:|^variant_coverage:|^sentence_precision:|^num_pred_sentences:|^num_gold_sentences:" "$L"
  echo "archived -> $DIR"
else
  echo "CRASH — tail:"; tail -n 30 "$L" | grep -v LiteLLM
  echo "archived (crash) -> $DIR"
fi
