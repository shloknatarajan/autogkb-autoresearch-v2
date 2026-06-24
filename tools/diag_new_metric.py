"""
Dev-set failure diagnostic for the REVISED (2026-06-23) paper-level meaning metric.

Why this exists: every prior miss diagnostic (`diag_misses.py`, `tools/diag_capture.py`)
was written against the OLD per-variant-key judge. The primary metric is now
`paper_meaning_capture` -- gold sentences pooled per paper, scored against the whole
predicted pool, keys ignored (eval.py). This tool answers the open question from the
2026-06-24 audit: of the ~45-50% of gold MEANING the champion still fails to capture,
how much is a fixable extraction error vs. a controlled-vocabulary phenotype rename vs.
genuinely unrecoverable from the markdown (supplement-only gold)?

It runs in two judge stages, DEV ONLY (never val -- val gold must stay unseen):

  Stage 1 (per paper): pool distinct gold + distinct predicted sentences exactly as
    eval.paper_meaning_capture does, then ask the judge, for each gold sentence, for a
    capture score AND a failure category when capture < 1.0:
      - captured            (>= PASS threshold; not a miss)
      - polarity_direction  : association present but polarity/direction reversed  [REAL BUG, prompt-fixable]
      - phenotype_mismatch  : variant+drug present but different/renamed outcome    [vocab; maybe unfixable from text]
      - qualifier_missing   : core association captured, missing comparison/population/genotype detail [partial]
      - not_predicted       : no predicted sentence covers this gold meaning at all

  Stage 2 (only for `not_predicted` golds): given the paper's markdown_content, judge
    whether the association is even stated/supported in the text:
      - recoverable         : present in markdown, the model simply missed it  [prompt/model-fixable]
      - unrecoverable       : not in markdown (supplement/external)            [caps the ceiling; NOT fixable here]

Output: a bucket histogram + a JSONL of every below-threshold gold sentence with its
category, so the next pipeline idea targets the largest *fixable* bucket instead of
guessing.

Usage:
    uv run python -m tools.diag_new_metric results/dev-champion-<ts>
    uv run python -m tools.diag_new_metric results/dev-champion-<ts> --judge-model gpt-5.4-mini --pass 0.999 --out scratch/diag.jsonl
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Reuse the harness's exact data, split, pooling and JSON parsing so the diagnostic
# scores the same thing eval.py does (no re-implementation drift).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import eval as E  # noqa: E402

CATEGORIES = [
    "polarity_direction",
    "phenotype_mismatch",
    "qualifier_missing",
    "not_predicted",
]

STAGE1_PROMPT = """You are auditing a pharmacogenomics information-extraction system on ONE paper.

You are given the paper's GOLD association sentences (the reference) and the system's \
PREDICTED association sentences (its entire output for the paper, keys ignored).

For EACH gold sentence, decide how much of its meaning is captured by the PREDICTED \
sentences taken together (recall), and -- when it is not fully captured -- WHY. Allow \
paraphrase/combine/split; a gold meaning is captured when some predicted sentence agrees \
on the substantive association: variant(s)/genotype(s), drug, phenotype/outcome, direction \
of effect, polarity (is vs is NOT associated), and comparison group when stated.

Assign exactly one category per gold sentence:
  - "captured": fully or near-fully captured (capture >= 0.9).
  - "polarity_direction": a predicted sentence matches the variant + drug + phenotype, \
but REVERSES the direction (increased vs decreased) or polarity (is vs is not associated). \
The most serious error.
  - "phenotype_mismatch": a predicted sentence matches the variant (and drug), but the \
phenotype/outcome differs or is renamed (e.g. paper term vs a controlled-vocabulary term), \
so credit is lost.
  - "qualifier_missing": the core association IS captured but an important qualifier is \
missing -- comparison group, population, or a genotype/diplotype/allele detail. (Partial.)
  - "not_predicted": no predicted sentence addresses this gold association at all.

Return JSON only, exactly one entry per gold sentence, gold_index matching the [i] labels:
{ "per_gold": [ { "gold_index": <int>, "capture": <0.0-1.0>, "category": "<one of the above>", "note": "<<=15 words>" }, ... ] }
"""

STAGE2_PROMPT = """You are checking whether a pharmacogenomics association is even stated in a paper's text.

You are given the full paper MARKDOWN and ONE gold association sentence (a curated \
PharmGKB-style standardized sentence). Decide whether the association it asserts -- the \
specific variant/genotype together with its outcome/phenotype and drug -- is supported by \
or derivable from the MARKDOWN TEXT provided (including its inline tables).

  - "recoverable": the variant and its association/outcome are present in this text, so a \
careful reader of the markdown could have extracted it.
  - "unrecoverable": the association is NOT in this text -- e.g. the variant or the finding \
appears only in a supplementary file/table not included here, so it cannot be extracted \
from the markdown at any skill level.

Be strict: if the specific variant is never mentioned in the text, it is "unrecoverable".

Return JSON only:
{ "verdict": "recoverable" | "unrecoverable", "evidence": "<<=20 words: quote or location, or why absent>" }
"""


def _completion(model, system, user):
    import litellm

    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


def stage1(gold_sents, pred_sents, model):
    """Per-gold capture + failure category for one paper."""
    user = E._judge_user_block(gold_sents, pred_sents)
    data = E._extract_json_object(_completion(model, STAGE1_PROMPT, user))
    rows = data.get("per_gold", []) if isinstance(data, dict) else []
    out = {}
    for m in rows if isinstance(rows, list) else []:
        if not isinstance(m, dict):
            continue
        gi = m.get("gold_index")
        if not isinstance(gi, int) or not (0 <= gi < len(gold_sents)):
            continue
        try:
            cap = max(0.0, min(1.0, float(m.get("capture", 0.0))))
        except (TypeError, ValueError):
            cap = 0.0
        cat = (
            m.get("category")
            if m.get("category") in (["captured"] + CATEGORIES)
            else None
        )
        out[gi] = {
            "capture": cap,
            "category": cat,
            "note": str(m.get("note", ""))[:120],
        }
    return out


def stage2(markdown, gold_sentence, model):
    """Is a not-predicted gold association even present in the markdown?"""
    user = f"MARKDOWN:\n{markdown}\n\n---\nGOLD ASSOCIATION SENTENCE:\n{gold_sentence}"
    data = E._extract_json_object(_completion(model, STAGE2_PROMPT, user))
    verdict = data.get("verdict") if isinstance(data, dict) else None
    if verdict not in ("recoverable", "unrecoverable"):
        verdict = "recoverable"  # conservative: assume the model could have got it
    return verdict, str(data.get("evidence", ""))[:160] if isinstance(
        data, dict
    ) else ""


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "generations", help="a DEV generations folder (e.g. results/dev-champion-<ts>)"
    )
    ap.add_argument("--judge-model", default=E.JUDGE_MODEL_DEFAULT)
    ap.add_argument(
        "--pass",
        dest="pass_thr",
        type=float,
        default=0.9,
        help="capture >= this counts as captured (default 0.9)",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="write per-miss JSONL here (default: scratch next to generations)",
    )
    args = ap.parse_args()

    bench = E.load_bench()
    gold_by_pmcid = {r["pmcid"]: r for r in bench}
    dev, _val = E.split_bench(bench)
    dev_ids = {r["pmcid"] for r in dev}
    gens = E.load_generations(args.generations)

    out_path = (
        Path(args.out) if args.out else Path(args.generations) / "_diag_misses.jsonl"
    )
    miss_rows = []

    cat_counts = Counter()
    n_gold_total = 0
    n_captured = 0
    paper_caps = []
    n_dev_scored = 0
    skipped_val = 0

    for pmcid, pred_groups in gens.items():
        if pmcid not in dev_ids:
            skipped_val += 1
            continue
        gold = gold_by_pmcid.get(pmcid)
        if gold is None:
            print(f"  {pmcid}: no gold, skip", file=sys.stderr)
            continue
        gold_sents = E._distinct_sentences(gold["variant_sentences"])
        if not gold_sents:
            continue
        n_dev_scored += 1
        pred_sents = E._distinct_sentences(pred_groups)
        res = stage1(gold_sents, pred_sents, args.judge_model)

        caps = []
        for gi, gs in enumerate(gold_sents):
            r = res.get(
                gi,
                {
                    "capture": 0.0,
                    "category": "not_predicted",
                    "note": "judge gave no entry",
                },
            )
            cap = r["capture"]
            caps.append(cap)
            n_gold_total += 1
            if cap >= args.pass_thr:
                n_captured += 1
                continue
            cat = r["category"] or (
                "not_predicted" if cap == 0.0 else "qualifier_missing"
            )
            row = {
                "pmcid": pmcid,
                "gold_index": gi,
                "gold": gs,
                "capture": round(cap, 3),
                "category": cat,
                "note": r["note"],
            }
            # Stage 2: only for not_predicted -- is it even in the text?
            if cat == "not_predicted":
                verdict, ev = stage2(gold["markdown_content"], gs, args.judge_model)
                row["recoverability"] = verdict
                row["evidence"] = ev
                cat_counts[f"not_predicted/{verdict}"] += 1
            else:
                cat_counts[cat] += 1
            miss_rows.append(row)
        pc = sum(caps) / len(caps) if caps else 0.0
        paper_caps.append(pc)
        print(
            f"  {pmcid}: paper_capture {pc:.3f} over {len(gold_sents)} distinct gold "
            f"({sum(1 for c in caps if c >= args.pass_thr)} captured)",
            file=sys.stderr,
        )

    out_path.write_text(
        "\n".join(json.dumps(r) for r in miss_rows) + ("\n" if miss_rows else "")
    )

    meaning_capture = sum(paper_caps) / len(paper_caps) if paper_caps else 0.0
    n_miss = n_gold_total - n_captured
    print("\n" + "=" * 64)
    print(f"DEV diagnostic on {args.generations}")
    print(
        f"  dev papers scored:        {n_dev_scored}"
        + (f"  (skipped {skipped_val} non-dev)" if skipped_val else "")
    )
    print(
        f"  meaning_capture (dev):    {meaning_capture:.3f}   [diag judge, pass>={args.pass_thr}]"
    )
    print(f"  distinct gold sentences:  {n_gold_total}")
    print(
        f"  captured (>= {args.pass_thr}):        {n_captured}  ({n_captured / n_gold_total:.1%})"
    )
    print(f"  MISSED:                   {n_miss}  ({n_miss / n_gold_total:.1%})")
    print("\n  Miss buckets (of the missed gold meanings):")
    fixable = {"polarity_direction", "qualifier_missing", "not_predicted/recoverable"}
    vocab = {"phenotype_mismatch"}
    unfixable = {"not_predicted/unrecoverable"}
    for cat, n in cat_counts.most_common():
        tag = (
            "FIXABLE (prompt/model)"
            if cat in fixable
            else "VOCAB (term norm)"
            if cat in vocab
            else "UNRECOVERABLE (not in markdown)"
            if cat in unfixable
            else ""
        )
        print(f"    {cat:34s} {n:3d}  ({n / n_miss:.0%} of misses)  {tag}")
    sum_fix = sum(n for c, n in cat_counts.items() if c in fixable)
    sum_vocab = sum(n for c, n in cat_counts.items() if c in vocab)
    sum_unfix = sum(n for c, n in cat_counts.items() if c in unfixable)
    print("\n  Rollup:")
    if n_miss:
        print(
            f"    fixable (extraction/recall):   {sum_fix:3d}  ({sum_fix / n_miss:.0%} of misses)"
        )
        print(
            f"    vocab (phenotype renaming):    {sum_vocab:3d}  ({sum_vocab / n_miss:.0%} of misses)"
        )
        print(
            f"    unrecoverable (markdown gap):  {sum_unfix:3d}  ({sum_unfix / n_miss:.0%} of misses)"
        )
    print(f"\n  per-miss detail -> {out_path}")
    print("=" * 64)


if __name__ == "__main__":
    main()
