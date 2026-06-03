"""
Evaluation harness for the autogkb sentence-bench autoresearch loop.

This file is the ground truth: it owns the dev/val split, the scoring (variant
coverage + per-variant meaning capture), and the LLM judge.

The benchmark is organized BY VARIANT: each paper's gold is a mapping
`{variant -> [standardized association sentences about that variant]}`. The
primary metric is `meaning_capture` -- for each gold variant we ask whether the
pipeline's predicted sentences for that variant capture the gold meanings, then
macro-average across variants (each variant counts equally) and across papers.

It scores a folder of GENERATIONS produced by `generate.py` (which runs
`annotation_pipeline.predict`). Each generation file is
`results/<name>/<pmcid>.json == {"pmcid", "variant_sentences": {variant: [...]}}`.
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

BENCH_PATH = Path(__file__).parent / "benchmarks" / "sentence_bench_by_variant.jsonl"
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


def normalize_groups(groups):
    """{variant -> [sentences]} keyed by normalized variant id, merging collisions.

    Used for both gold and predicted side so a gold variant can be looked up by
    its canonical id regardless of how the pipeline spelled it.
    """
    out = {}
    for k, sents in (groups or {}).items():
        nk = normalize_variant(k)
        bucket = out.setdefault(nk, [])
        for s in sents or []:
            s = str(s)
            if s.strip() and s not in bucket:
                bucket.append(s)
    return out


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
    """Be forgiving about pipeline output shape; never crash the harness on it.

    Returns the predicted {variant -> [sentences]} mapping (un-normalized keys).
    """
    if not isinstance(out, dict):
        return {}
    vs = out.get("variant_sentences")
    if not isinstance(vs, dict):
        return {}
    groups = {}
    for k, sents in vs.items():
        key = str(k).strip()
        if not key:
            continue
        groups[key] = [str(s) for s in (sents or []) if str(s).strip()]
    return groups


def load_generations(generations_dir):
    """Read every <pmcid>.json from a results folder into {pmcid: {variant: [sentences]}}."""
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
    bench = load_bench()
    gold_by_pmcid = {r["pmcid"]: r for r in bench}
    dev, val = split_bench(bench)
    dev_ids = {r["pmcid"] for r in dev}
    gens = load_generations(generations_dir)

    matched_var = total_var = 0
    matched_sent = total_gold = total_pred_considered = 0
    paper_macros = []
    scored = on_dev = 0

    t0 = time.time()
    for pmcid, pred_groups in gens.items():
        gold = gold_by_pmcid.get(pmcid)
        if gold is None:
            print(
                f"  {pmcid}: WARNING no gold for this pmcid, skipping", file=sys.stderr
            )
            continue
        scored += 1
        on_dev += pmcid in dev_ids

        gold_groups = gold["variant_sentences"]
        pred_norm = normalize_groups(pred_groups)

        # Variant coverage (recall over variant keys; extra predicted variants free).
        mv, tv = variant_coverage_counts(pred_norm.keys(), gold_groups.keys())
        matched_var += mv
        total_var += tv

        # Meaning capture per variant: for each gold variant, how many of its gold
        # association sentences are captured by the pipeline's sentences for that
        # variant? Macro-average across the paper's variants (each equal weight).
        captures = []
        paper_matched = paper_gold = 0
        for v, gold_sents in gold_groups.items():
            if (
                not gold_sents
            ):  # declared-but-sentence-less variant: only counts for coverage
                continue
            pred_sents = pred_norm.get(normalize_variant(v), [])
            ms = judge_sentences(gold_sents, pred_sents, judge_model)
            captures.append(ms / len(gold_sents))
            matched_sent += ms
            total_gold += len(gold_sents)
            total_pred_considered += len(pred_sents)
            paper_matched += ms
            paper_gold += len(gold_sents)
        paper_macro = sum(captures) / len(captures) if captures else 0.0
        paper_macros.append(paper_macro)

        print(
            f"  {pmcid}: variants {mv}/{tv}, meaning_capture {paper_macro:.3f} "
            f"(sentences matched {paper_matched}/{paper_gold} over {len(captures)} variants)",
            file=sys.stderr,
        )

    # Primary metric: macro per-variant meaning capture, averaged across papers.
    meaning_capture = sum(paper_macros) / len(paper_macros) if paper_macros else 0.0
    variant_coverage = matched_var / total_var if total_var else 0.0
    # Micro coverage (per gold variant-sentence, not per variant) -- informational.
    sentence_coverage = matched_sent / total_gold if total_gold else 0.0
    sent_precision = (
        matched_sent / total_pred_considered if total_pred_considered else 0.0
    )  # informational

    if on_dev:
        print(
            f"  NOTE: {on_dev}/{scored} scored papers are DEV papers (val is the honest metric)",
            file=sys.stderr,
        )

    print("---")
    print(f"meaning_capture:    {meaning_capture:.3f}")  # PRIMARY (macro per variant)
    print(f"variant_coverage:   {variant_coverage:.3f}")
    print(f"sentence_coverage:  {sentence_coverage:.3f}")  # micro, informational
    print(f"sentence_precision: {sent_precision:.3f}")
    print(f"num_papers:         {scored}")
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
