"""Diagnose meaning_capture misses on the DEV split.

For each dev paper + gold variant, runs the SAME batch judge eval.py uses and
prints which gold sentences went unmatched, alongside the predicted sentences
for that variant. Helps see whether misses are missing-variant, missing-sentence,
or wrong direction/polarity/phenotype wording.

Usage:
    uv run tools/diag_capture.py results/dev_<ts>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import (
    build_judge_messages,
    load_bench,
    normalize_groups,
    normalize_variant,
    parse_judge_matches,
    split_bench,
    JUDGE_MODEL_DEFAULT,
)
from eval import load_generations


def main():
    gens = load_generations(sys.argv[1])
    model = sys.argv[2] if len(sys.argv) > 2 else JUDGE_MODEL_DEFAULT
    import litellm

    dev, _ = split_bench(load_bench())
    gold_by = {r["pmcid"]: r for r in dev}

    tot_gold = tot_matched = 0
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
            matched = set()
            if pred_sents:
                resp = litellm.completion(
                    model=model,
                    messages=build_judge_messages(gold_sents, pred_sents),
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                pairs = parse_judge_matches(
                    resp.choices[0].message.content or "",
                    len(gold_sents),
                    len(pred_sents),
                )
                matched = {g for g, _ in pairs}
            tot_gold += len(gold_sents)
            tot_matched += len(matched)
            print(
                f"  [{v}] {len(matched)}/{len(gold_sents)} (pred has {len(pred_sents)})"
            )
            for i, g in enumerate(gold_sents):
                if i not in matched:
                    print(f"      MISS: {g}")
    print(
        f"\nDEV micro capture: {tot_matched}/{tot_gold} = {tot_matched / tot_gold:.3f}"
    )


if __name__ == "__main__":
    main()
