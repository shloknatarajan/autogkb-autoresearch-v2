"""Diagnose meaning_capture misses on the DEV split.

For each dev paper + gold variant, runs eval.py's diagnostic judge and prints
which gold sentences went uncaptured, alongside the predicted sentences for that
variant. Helps see whether misses are missing-variant, missing-sentence, or
wrong direction/polarity/phenotype wording.

Usage:
    uv run tools/diag_capture.py results/dev_<ts> [judge_model]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from eval import (
    JUDGE_MODEL_DEFAULT,
    judge_per_gold,
    load_bench,
    load_generations,
    normalize_groups,
    normalize_variant,
    split_bench,
)

load_dotenv()


def main():
    gens = load_generations(sys.argv[1])
    model = sys.argv[2] if len(sys.argv) > 2 else JUDGE_MODEL_DEFAULT

    dev, _ = split_bench(load_bench())
    gold_by = {r["pmcid"]: r for r in dev}

    tot_gold = tot_captured = 0.0
    for pmcid, pred_groups in gens.items():
        r = gold_by.get(pmcid)
        if r is None:
            continue
        pred_norm = normalize_groups(pred_groups)
        print(f"\n===== {pmcid} =====")
        for v, gold_sents in r["variant_sentences"].items():
            if not gold_sents:
                continue
            pred_sents = pred_norm.get(normalize_variant(v), [])
            caps = judge_per_gold(gold_sents, pred_sents, model)
            captured = sum(1 for c in caps if c >= 0.5)
            tot_gold += len(gold_sents)
            tot_captured += sum(caps)
            print(f"  [{v}] {captured}/{len(gold_sents)} (pred has {len(pred_sents)})")
            for s, c in zip(gold_sents, caps):
                if c < 0.5:
                    print(f"      MISS ({c:.2f}): {s}")
    if tot_gold:
        print(
            f"\nDEV micro capture: {tot_captured:.1f}/{tot_gold:.0f} = "
            f"{tot_captured / tot_gold:.3f}"
        )


if __name__ == "__main__":
    main()
