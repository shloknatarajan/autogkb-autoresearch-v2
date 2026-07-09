"""Per-gold-sentence failure analysis for two pipelines on a split.

Writes a JSON blob consumed by the HTML report. For each gold variant:
  - if the variant key is absent from the prediction -> MISSING_VARIANT
  - else run eval.judge_per_gold to get a 0..1 capture per gold sentence

Usage: uv run failure_analysis.py <split> <label>=<gendir> [<label>=<gendir> ...] > out.json
"""

import json
import os
import re
import sys

sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv

load_dotenv()

from eval import (  # noqa: E402
    JUDGE_MODEL_DEFAULT,
    judge_per_gold,
    load_bench,
    load_generations,
    normalize_groups,
    split_bench,
)


def variant_class(v):
    s = str(v).strip().upper()
    if re.fullmatch(r"RS\d+", s):
        return "rsID"
    if s.startswith("HLA"):
        return "HLA"
    if re.search(r"\*0*1$", s):
        return "wildtype *1"
    if "*" in s:
        return "star allele"
    return "other"


def bucket(c):
    if c >= 0.75:
        return "captured"
    if c >= 0.25:
        return "partial"
    return "lost"


def main():
    split = sys.argv[1]
    pipelines = [a.split("=", 1) for a in sys.argv[2:]]

    dev, val = split_bench(load_bench())
    records = {"dev": dev, "val": val}[split]
    gold_by = {r["pmcid"]: r for r in records}

    out = {"split": split, "judge_model": JUDGE_MODEL_DEFAULT, "pipelines": {}}

    for label, gendir in pipelines:
        gens = load_generations(gendir)
        rows = []
        for pmcid, rec in sorted(gold_by.items()):
            gold = normalize_groups(rec["variant_sentences"])
            pred = normalize_groups(gens.get(pmcid, {}))
            for gv, gsents in sorted(gold.items()):
                if not gsents:
                    continue  # no gold sentences -> not scored for meaning
                psents = pred.get(gv)
                if psents is None:
                    for i, gs in enumerate(gsents):
                        rows.append({
                            "pmcid": pmcid, "variant": gv, "vclass": variant_class(gv),
                            "gold": gs, "pred": [], "capture": 0.0,
                            "bucket": "lost", "mode": "MISSING_VARIANT",
                        })
                    continue
                caps = judge_per_gold(gsents, psents, JUDGE_MODEL_DEFAULT)
                for i, gs in enumerate(gsents):
                    c = float(caps[i]) if i < len(caps) else 0.0
                    b = bucket(c)
                    rows.append({
                        "pmcid": pmcid, "variant": gv, "vclass": variant_class(gv),
                        "gold": gs, "pred": psents, "capture": round(c, 3),
                        "bucket": b,
                        "mode": "OK" if b == "captured" else (
                            "SENTENCE_LOST" if b == "lost" else "QUALIFIER_DROPPED"),
                    })
            print(f"  {label} {pmcid} done", file=sys.stderr)

        # coverage: gold variant keys matched (all gold variants, incl. empty-sentence ones)
        cov_m = cov_t = 0
        missing_keys = []
        for pmcid, rec in gold_by.items():
            gold_all = set(normalize_groups(rec["variant_sentences"]))
            pred_all = set(normalize_groups(gens.get(pmcid, {})))
            cov_m += len(gold_all & pred_all)
            cov_t += len(gold_all)
            for k in sorted(gold_all - pred_all):
                missing_keys.append({"pmcid": pmcid, "variant": k, "vclass": variant_class(k)})

        n_pred_keys = sum(len(g) for g in gens.values())
        n_pred_sents = sum(
            sum(len(v) for v in g.values()) for g in gens.values()
        )

        out["pipelines"][label] = {
            "gendir": gendir,
            "rows": rows,
            "coverage": {"matched": cov_m, "total": cov_t},
            "missing_keys": missing_keys,
            "pred_keys": n_pred_keys,
            "pred_sentences": n_pred_sents,
        }

    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
