"""Diagnose MISSED gold variants on the dev set (val gold is off-limits).

Reconstructs iter15's predicted variant set for each dev paper:
    predicted = (LM variants from an existing dev generation) UNION (regex scan)
then compares to gold under eval.normalize_variant, and for each miss classifies:
  - NORMALIZATION: a predicted variant is "close" (same digits / same gene+allele
    ignoring punctuation/case) but normalizes differently -> a normalization gap.
  - EXTRACTION:   nothing in predicted resembles it -> we never found it.

Usage: uv run diag_variants.py results/dev_<ts>
"""
import json
import re
import sys
from pathlib import Path

from eval import load_bench, split_bench, normalize_variant
from tools.regex_variants import extract_all_variants

gen_dir = Path(sys.argv[1])
dev, _ = split_bench(load_bench())
gold_by = {r["pmcid"]: r for r in dev}


def loose_key(v):
    """Very permissive key: digits for rs, else alnum-only uppercase."""
    s = str(v).strip()
    m = re.fullmatch(r"[rR][sS]\s*0*(\d+)", s)
    if m:
        return "RS" + m.group(1)
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


tot_gold = tot_missed = tot_norm = tot_extract = 0
norm_examples, extract_examples = [], []

for f in sorted(gen_dir.glob("*.json")):
    data = json.loads(f.read_text())
    pmcid = data.get("pmcid") or f.stem
    if pmcid not in gold_by:
        continue
    gold = gold_by[pmcid]["variants"]
    md = gold_by[pmcid]["markdown_content"]
    lm_pred = data.get("variants", [])
    regex_pred = extract_all_variants(md)
    pred = list(lm_pred) + list(regex_pred)

    pred_norm = {normalize_variant(v) for v in pred}
    pred_loose = {loose_key(v) for v in pred}

    for g in gold:
        tot_gold += 1
        if normalize_variant(g) in pred_norm:
            continue
        tot_missed += 1
        if loose_key(g) in pred_loose:                # found but normalized differently
            tot_norm += 1
            culprits = [p for p in pred if loose_key(p) == loose_key(g)]
            if len(norm_examples) < 25:
                norm_examples.append((pmcid, g, normalize_variant(g),
                                      sorted({(p, normalize_variant(p)) for p in culprits})))
        else:
            tot_extract += 1
            in_text = g.lower().replace(" ", "") in md.lower().replace(" ", "")
            if len(extract_examples) < 25:
                extract_examples.append((pmcid, g, "in_text" if in_text else "NOT_in_text"))

print(f"DEV gold variants: {tot_gold} | missed: {tot_missed} "
      f"(coverage {1-tot_missed/tot_gold:.3f})")
print(f"  -> NORMALIZATION misses (predicted but wrong canonical form): {tot_norm}")
print(f"  -> EXTRACTION   misses (not predicted at all):                {tot_extract}")

print("\n=== NORMALIZATION misses: gold -> its normal form  ||  what we predicted ===")
for pmcid, g, gn, culprits in norm_examples:
    print(f"  [{pmcid}] gold {g!r} -> {gn!r}")
    for raw, rn in culprits:
        print(f"        predicted {raw!r} -> {rn!r}")

print("\n=== EXTRACTION misses: gold variant (and whether the literal token is in the text) ===")
for pmcid, g, status in extract_examples:
    print(f"  [{pmcid}] {g!r}  [{status}]")
