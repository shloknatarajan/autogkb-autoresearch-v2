"""
Read-only evaluation harness for the autogkb sentence-bench autoresearch loop.

DO NOT MODIFY (see program.md). This file is the ground truth: it owns the
dev/val split, the scoring (variant coverage + sentence F1), and the LLM judge.

It scores a folder of GENERATIONS produced by `generate.py` (which runs
`annotation_pipeline.predict`). Each generation file is
`results/<name>/<pmcid>.json == {"pmcid", "variants": [...], "sentences": [...]}`.
Generation and scoring are decoupled, so the same generations can be re-scored
without regenerating.

Usage:
    uv run eval.py results/baseline                 # score that generation folder
    uv run eval.py results/baseline --judge-model gpt-4o
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BENCH_PATH = Path(__file__).parent / "benchmarks" / "sentence_bench_collapsed.jsonl"
JUDGE_MODEL_DEFAULT = "gpt-5.4-mini"

JUDGE_PROMPT = """You are grading a pharmacogenomics information-extraction system. You are given \
two lists of "standardized association sentences" about the SAME paper:

GOLD sentences (the reference) and PREDICTED sentences (the system output).

Each sentence asserts a single association between a genetic variant/genotype and \
an outcome. Match a PREDICTED sentence to a GOLD sentence ONLY IF they assert the \
same association. They must agree on ALL of:
  - the variant(s)/genotype(s) (e.g. rs9923231, CYP2C19*2, *1/*2)
  - the drug(s) or substance involved (if any)
  - the phenotype / outcome (e.g. dose, MACE, toxicity, metabolizer status)
  - the DIRECTION of effect (increased vs decreased / higher vs lower)
  - the POLARITY ("is associated" vs "is NOT associated")
  - the comparison group / comparison allele, when stated

Differences in wording, order, or formatting do NOT matter -- only the asserted \
meaning. Be strict about direction and polarity: "is associated with increased" \
and "is not associated" (or "decreased") describe DIFFERENT associations and MUST \
NOT be matched.

Each gold sentence matches at most one predicted sentence and vice versa (one-to-one).

Return JSON only:
{ "matches": [ { "gold_index": <int>, "pred_index": <int> }, ... ] }
Include only true matches. Omit anything unmatched."""


# --------------------------------------------------------------------------- #
# Data + split
# --------------------------------------------------------------------------- #
def load_bench(path=BENCH_PATH):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def split_bench(records):
    """Deterministic dev/val split: alternate over pmcid-sorted records.

    The agent may inspect `dev` (papers + gold) while iterating. `val` is the
    held-out, scored set -- never inspect or hard-code its gold labels.
    """
    recs = sorted(records, key=lambda r: r["pmcid"])
    dev = [r for i, r in enumerate(recs) if i % 2 == 0]
    val = [r for i, r in enumerate(recs) if i % 2 == 1]
    return dev, val


# --------------------------------------------------------------------------- #
# Variant coverage (recall only -- extra predicted variants are not penalized)
# --------------------------------------------------------------------------- #
def normalize_variant(v):
    """Canonicalize a variant id for comparison.

    rsIDs -> 'rs<digits>' (lowercased, leading zeros stripped); everything else
    -> uppercased with internal whitespace removed (so 'CYP2C19 *2' == 'CYP2C19*2').
    """
    s = str(v).strip()
    m = re.fullmatch(r"[rR][sS]\s*0*(\d+)", s)
    if m:
        return "rs" + m.group(1)
    return re.sub(r"\s+", "", s).upper()


def variant_coverage_counts(pred_variants, gold_variants):
    """Return (matched, total_gold) for one paper. Coverage = matched / total_gold."""
    gold = {normalize_variant(v) for v in gold_variants}
    pred = {normalize_variant(v) for v in pred_variants}
    if not gold:
        return 0, 0
    return len(gold & pred), len(gold)


# --------------------------------------------------------------------------- #
# Sentence F1 via batch LLM judge
# --------------------------------------------------------------------------- #
def build_judge_messages(gold, pred):
    gold_block = "\n".join(f"[{i}] {s}" for i, s in enumerate(gold)) or "(none)"
    pred_block = "\n".join(f"[{i}] {s}" for i, s in enumerate(pred)) or "(none)"
    user = f"GOLD sentences:\n{gold_block}\n\nPREDICTED sentences:\n{pred_block}"
    return [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": user},
    ]


def _extract_json_object(text):
    """Pull the first balanced {...} JSON object out of a model response."""
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def parse_judge_matches(text, n_gold, n_pred):
    """Parse judge output into validated one-to-one (gold_index, pred_index) pairs."""
    data = _extract_json_object(text)
    raw = data.get("matches", []) if isinstance(data, dict) else []
    seen_gold, seen_pred, pairs = set(), set(), []
    for m in raw:
        if not isinstance(m, dict):
            continue
        g, p = m.get("gold_index"), m.get("pred_index")
        if not isinstance(g, int) or not isinstance(p, int):
            continue
        if not (0 <= g < n_gold) or not (0 <= p < n_pred):
            continue
        if g in seen_gold or p in seen_pred:  # enforce one-to-one
            continue
        seen_gold.add(g)
        seen_pred.add(p)
        pairs.append((g, p))
    return pairs


def judge_sentences(gold, pred, model):
    """Return the number of matched (gold, pred) sentence pairs for one paper."""
    if not gold or not pred:
        return 0
    import litellm

    resp = litellm.completion(
        model=model,
        messages=build_judge_messages(gold, pred),
        temperature=0,
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content or ""
    return len(parse_judge_matches(text, len(gold), len(pred)))


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def coerce_prediction(out):
    """Be forgiving about pipeline output shape; never crash the harness on it."""
    if not isinstance(out, dict):
        return [], []
    variants = out.get("variants") or []
    sentences = out.get("sentences") or []
    variants = [str(v) for v in variants if str(v).strip()]
    sentences = [str(s) for s in sentences if str(s).strip()]
    return variants, sentences


def load_generations(generations_dir):
    """Read every <pmcid>.json from a results folder into {pmcid: (variants, sentences)}."""
    path = Path(generations_dir)
    if not path.is_dir():
        sys.exit(f"generations folder not found: {path}")
    gens = {}
    for f in sorted(path.glob("*.json")):
        data = json.loads(f.read_text())
        pmcid = data.get("pmcid") or f.stem
        gens[pmcid] = coerce_prediction(data)
    if not gens:
        sys.exit(f"no *.json generations in {path}")
    return gens


def evaluate(generations_dir, judge_model):
    gold_by_pmcid = {r["pmcid"]: r for r in load_bench()}
    dev, val = split_bench(load_bench())
    dev_ids = {r["pmcid"] for r in dev}
    gens = load_generations(generations_dir)

    matched_var = total_var = 0
    matched_sent = total_pred = total_gold = 0
    scored = on_dev = 0

    t0 = time.time()
    for pmcid, (pred_variants, pred_sentences) in gens.items():
        gold = gold_by_pmcid.get(pmcid)
        if gold is None:
            print(
                f"  {pmcid}: WARNING no gold for this pmcid, skipping", file=sys.stderr
            )
            continue
        scored += 1
        on_dev += pmcid in dev_ids

        mv, tv = variant_coverage_counts(pred_variants, gold["variants"])
        matched_var += mv
        total_var += tv

        ms = judge_sentences(gold["sentences"], pred_sentences, judge_model)
        matched_sent += ms
        total_pred += len(pred_sentences)
        total_gold += len(gold["sentences"])

        print(
            f"  {pmcid}: variants {mv}/{tv}, "
            f"sentences matched {ms} (pred {len(pred_sentences)}, gold {len(gold['sentences'])})",
            file=sys.stderr,
        )

    # Coverage (recall) is the metric for both variants and sentences: did we
    # find what's in the gold set? Extra predicted items are NOT penalized.
    variant_coverage = matched_var / total_var if total_var else 0.0
    sentence_coverage = matched_sent / total_gold if total_gold else 0.0
    sent_precision = matched_sent / total_pred if total_pred else 0.0  # informational

    if on_dev:
        print(
            f"  NOTE: {on_dev}/{scored} scored papers are DEV papers (val is the honest metric)",
            file=sys.stderr,
        )

    print("---")
    print(f"sentence_coverage:  {sentence_coverage:.3f}")
    print(f"variant_coverage:   {variant_coverage:.3f}")
    print(f"sentence_precision: {sent_precision:.3f}")
    print(f"num_papers:         {scored}")
    print(f"num_pred_sentences: {total_pred}")
    print(f"num_gold_sentences: {total_gold}")
    print(f"generations:        {generations_dir}")
    print(f"judge_model:        {judge_model}")
    print(f"total_seconds:      {time.time() - t0:.1f}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "generations",
        help="path to a results folder written by generate.py, e.g. results/baseline",
    )
    parser.add_argument(
        "--judge-model",
        default=JUDGE_MODEL_DEFAULT,
        help=f"litellm model string for the judge (default: {JUDGE_MODEL_DEFAULT})",
    )
    args = parser.parse_args()
    evaluate(args.generations, args.judge_model)


if __name__ == "__main__":
    main()
