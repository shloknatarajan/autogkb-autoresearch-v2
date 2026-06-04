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
two lists of "standardized association sentences" about the SAME paper and the SAME genetic variant:

GOLD sentences (the reference) and PREDICTED sentences (the system output).

Each sentence asserts an association between a genetic variant/genotype and \
an outcome. Evaluate what fraction of the meaning in the GOLD sentences is captured \
by the PREDICTED sentences.

Score only recall of the gold meanings. Extra predicted associations are not penalized \
unless they make it unclear whether a gold meaning is actually captured.

Be critical, but allow multiple phrasings of the same association. A prediction can \
capture a gold meaning even when it combines, splits, reorders, or paraphrases the \
gold sentence. It must still agree on the substantive association:
  - the variant(s)/genotype(s), including alleles or diplotypes when relevant
  - the drug(s) or substance involved, if any
  - the phenotype / outcome (e.g. dose, MACE, toxicity, metabolizer status)
  - the direction of effect (increased vs decreased / higher vs lower)
  - the polarity ("is associated" vs "is NOT associated")
  - the comparison group / comparison allele, when stated

Do NOT give credit when direction or polarity is reversed, when a different phenotype \
or drug is substituted, or when a genotype-specific finding is generalized in a way \
that loses the gold meaning. Give partial credit for partially captured gold meaning, \
such as the right association but missing an important qualifier, population, comparison, \
or genotype detail.

Return JSON only:
{ "meaning_capture": <number from 0.0 to 1.0> }

"""

# Diagnostic-only judge: same rubric, but scores EACH gold sentence individually
# so tooling can show which gold meanings were missed. NOT used for the metric.
JUDGE_DIAG_PROMPT = """You are grading a pharmacogenomics information-extraction system. You are given \
two lists of "standardized association sentences" about the SAME paper and the SAME genetic variant:

GOLD sentences (the reference) and PREDICTED sentences (the system output).

For EACH gold sentence, judge how much of its meaning is captured by the PREDICTED \
sentences taken together (recall). Allow multiple phrasings of the same association: a \
prediction can capture a gold meaning even when it combines, splits, reorders, or \
paraphrases the gold sentence, as long as it agrees on the substantive association:
  - the variant(s)/genotype(s), including alleles or diplotypes when relevant
  - the drug(s) or substance involved, if any
  - the phenotype / outcome (e.g. dose, MACE, toxicity, metabolizer status)
  - the direction of effect (increased vs decreased / higher vs lower)
  - the polarity ("is associated" vs "is NOT associated")
  - the comparison group / comparison allele, when stated

Give 0.0 when direction or polarity is reversed, a different phenotype or drug is \
substituted, or the gold meaning is otherwise absent. Give partial credit (between 0.0 \
and 1.0) when the core association is captured but an important qualifier, population, \
comparison, or genotype detail is missing.

Return JSON only, with exactly one entry per gold sentence:
{ "per_gold": [ { "gold_index": <int>, "capture": <number from 0.0 to 1.0> }, ... ] }
"""


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
# Meaning capture via batch LLM judge
# --------------------------------------------------------------------------- #
def _judge_user_block(gold, pred):
    gold_block = "\n".join(f"[{i}] {s}" for i, s in enumerate(gold)) or "(none)"
    pred_block = "\n".join(f"[{i}] {s}" for i, s in enumerate(pred)) or "(none)"
    return f"GOLD sentences:\n{gold_block}\n\nPREDICTED sentences:\n{pred_block}"


def build_judge_messages(gold, pred):
    return [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": _judge_user_block(gold, pred)},
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


def parse_judge_capture(text):
    """Parse judge output into a clamped 0..1 meaning-capture score."""
    data = _extract_json_object(text)
    if not isinstance(data, dict):
        return 0.0
    raw = data.get("meaning_capture", data.get("capture", data.get("score", 0.0)))
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if score > 10.0 and score <= 100.0:
        score /= 100.0
    return max(0.0, min(1.0, score))


def parse_judge_per_gold(text, n_gold):
    """Parse the diagnostic judge into a list of n_gold capture floats (0..1).

    Gold sentences with no entry (or an out-of-range / unparseable one) default to 0.0.
    """
    data = _extract_json_object(text)
    out = [0.0] * n_gold
    raw = data.get("per_gold", []) if isinstance(data, dict) else []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        g = m.get("gold_index")
        if not isinstance(g, int) or not (0 <= g < n_gold):
            continue
        try:
            score = float(m.get("capture", 0.0))
        except (TypeError, ValueError):
            continue
        if score > 10.0 and score <= 100.0:
            score /= 100.0
        out[g] = max(0.0, min(1.0, score))
    return out


def judge_sentences(gold, pred, model):
    """Return 0..1 fraction of gold sentence meaning captured for one variant."""
    if not gold or not pred:
        return 0.0
    import litellm

    resp = litellm.completion(
        model=model,
        messages=build_judge_messages(gold, pred),
        temperature=0,
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content or ""
    return parse_judge_capture(text)


def judge_per_gold(gold, pred, model):
    """Per-gold-sentence capture (0..1) for one variant -- for diagnostics only.

    The metric uses judge_sentences (one aggregate score); this gives a per-sentence
    breakdown so diag tools can show which gold meanings were missed.
    """
    if not gold:
        return []
    if not pred:
        return [0.0] * len(gold)
    import litellm

    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": JUDGE_DIAG_PROMPT},
            {"role": "user", "content": _judge_user_block(gold, pred)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content or ""
    return parse_judge_per_gold(text, len(gold))


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
    captured_gold_equiv = total_gold = 0
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
            capture = judge_sentences(gold_sents, pred_sents, judge_model)
            captures.append(capture)
            captured_gold_equiv += capture * len(gold_sents)
            total_gold += len(gold_sents)
            paper_matched += capture * len(gold_sents)
            paper_gold += len(gold_sents)
        paper_macro = sum(captures) / len(captures) if captures else 0.0
        paper_macros.append(paper_macro)

        print(
            f"  {pmcid}: variants {mv}/{tv}, meaning_capture {paper_macro:.3f} "
            f"(gold-equivalent captured {paper_matched:.1f}/{paper_gold} over {len(captures)} variants)",
            file=sys.stderr,
        )

    # Primary metric: macro per-variant meaning capture, averaged across papers.
    meaning_capture = sum(paper_macros) / len(paper_macros) if paper_macros else 0.0
    variant_coverage = matched_var / total_var if total_var else 0.0
    # Micro coverage (per gold variant-sentence, not per variant) -- informational.
    sentence_coverage = captured_gold_equiv / total_gold if total_gold else 0.0

    if on_dev:
        print(
            f"  NOTE: {on_dev}/{scored} scored papers are DEV papers (val is the honest metric)",
            file=sys.stderr,
        )

    print("---")
    print(f"meaning_capture:    {meaning_capture:.3f}")  # PRIMARY (macro per variant)
    print(f"variant_coverage:   {variant_coverage:.3f}")
    print(f"sentence_coverage:  {sentence_coverage:.3f}")  # micro, informational
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
