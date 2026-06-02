"""Diagnostic: show which DEV gold sentences our predictions FAIL to match.
Usage: uv run diag_misses.py results/dev_<ts>
Uses eval.py's own judge so the miss analysis matches the real scoring.
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from eval import (JUDGE_MODEL_DEFAULT, build_judge_messages, load_bench,
                  parse_judge_matches, split_bench)

load_dotenv()
import litellm

gen_dir = Path(sys.argv[1])
dev, _ = split_bench(load_bench())
gold_by = {r["pmcid"]: r for r in dev}

for f in sorted(gen_dir.glob("*.json")):
    data = json.loads(f.read_text())
    pmcid = data.get("pmcid") or f.stem
    if pmcid not in gold_by:
        continue
    gold = gold_by[pmcid]["sentences"]
    pred = data.get("sentences", [])
    if not gold:
        continue
    resp = litellm.completion(model=JUDGE_MODEL_DEFAULT,
                              messages=build_judge_messages(gold, pred),
                              temperature=0, response_format={"type": "json_object"})
    pairs = parse_judge_matches(resp.choices[0].message.content or "", len(gold), len(pred))
    matched_gold = {g for g, _ in pairs}
    missed = [gold[i] for i in range(len(gold)) if i not in matched_gold]
    print(f"\n==== {pmcid}: matched {len(matched_gold)}/{len(gold)} gold (pred={len(pred)})")
    for m in missed:
        print("  MISS:", m)
