"""Diagnostic: show which DEV gold sentences our predictions FAIL to capture.
Usage: uv run diag_misses.py results/dev_<ts> [judge_model]
Uses eval.py's diagnostic judge so the miss analysis follows the real scoring rubric.
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from eval import (
    JUDGE_MODEL_DEFAULT,
    judge_per_gold,
    load_bench,
    normalize_groups,
    normalize_variant,
    split_bench,
)

load_dotenv()

gen_dir = Path(sys.argv[1])
model = sys.argv[2] if len(sys.argv) > 2 else JUDGE_MODEL_DEFAULT
dev, _ = split_bench(load_bench())
gold_by = {r["pmcid"]: r for r in dev}

for f in sorted(gen_dir.glob("*.json")):
    data = json.loads(f.read_text())
    pmcid = data.get("pmcid") or f.stem
    if pmcid not in gold_by:
        continue
    gold_groups = gold_by[pmcid]["variant_sentences"]
    pred_norm = normalize_groups(data.get("variant_sentences", {}))

    missed, n_gold = [], 0
    for v, gold_sents in gold_groups.items():
        if not gold_sents:
            continue
        n_gold += len(gold_sents)
        pred_sents = pred_norm.get(normalize_variant(v), [])
        caps = judge_per_gold(gold_sents, pred_sents, model)
        for s, c in zip(gold_sents, caps):
            if c < 0.5:
                missed.append(f"[{v}] {s}")
    print(f"\n==== {pmcid}: missed {len(missed)}/{n_gold} gold sentences")
    for m in missed:
        print("  MISS:", m)
