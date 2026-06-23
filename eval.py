"""
Evaluation harness for the autogkb sentence-bench autoresearch loop.

This file is the ground truth: it owns the dev/val split, the scoring, and the
LLM judge. The benchmark is organized BY VARIANT: each paper's gold is a mapping
`{variant -> [standardized association sentences about that variant]}`.

PRIMARY metrics (revised to measure extraction skill, not PharmGKB house style --
see docs/ANNOTATION_AMBIGUITY.md):
  - `meaning_capture`  -- PAPER-LEVEL, representation-invariant. All of a paper's
    distinct gold sentences are pooled and scored against the pipeline's full
    predicted-sentence pool (keys ignored), so a correct association filed under a
    different-but-valid variant key is no longer a miss. Macro-averaged over papers.
  - `variant_coverage` -- recall over gold variant keys, accepting rsID<->star-allele
    equivalence (a star allele matches its defining rsID and vice versa).

SECONDARY metrics (PharmGKB-convention adherence, kept for comparison):
  - `meaning_capture_perkey` -- the old per-variant macro (one judge call per gold
    variant, sentences matched only under the same key).
  - `variant_coverage_strict` -- old exact-representation key match.
  - `sentence_coverage` -- micro, informational.

It scores a folder of GENERATIONS produced by `generate.py` (which runs
`annotation_pipeline.predict`). Each generation file is
`results/<name>/<pmcid>.json == {"pmcid", "variant_sentences": {variant: [...]}}`.
Generation and scoring are decoupled, so the same generations can be re-scored
without regenerating.

NOTE: this revision changes what `meaning_capture` measures, so its values are NOT
comparable to pre-revision runs (jun1..jun5). Re-baseline before trusting deltas.

Usage:
    uv run eval.py results/baseline                 # score that generation folder
    uv run eval.py results/baseline --judge-model gpt-4o
    uv run eval.py results/baseline --no-perkey     # primary metrics only (cheaper)
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
    """Return (matched, total_gold) for one paper. Coverage = matched / total_gold.

    STRICT representation match (kept as the secondary `variant_coverage_strict`);
    the primary coverage uses `variant_coverage_match`, which accepts rsID<->star
    equivalence.
    """
    gold = {normalize_variant(v) for v in gold_variants}
    pred = {normalize_variant(v) for v in pred_variants}
    if not gold:
        return 0, 0
    return len(gold & pred), len(gold)


# --------------------------------------------------------------------------- #
# rsID <-> star-allele identity (Recommendation 2)
# --------------------------------------------------------------------------- #
# A star allele and its single defining/tag SNP are the SAME biological variant,
# but a paper (and an agent reading it) may report either form, and the gold picks
# only one representation per annotation -- so an agent that emits the other form is
# wrongly scored as a miss. This table lets coverage accept either form.
#
# CURATED, CONSERVATIVE, AUDITABLE. Only star alleles whose identity is fixed by a
# SINGLE canonical defining SNP are listed (verified against PharmVar / CPIC).
# Deliberately OMITTED -- because a wrong equivalence silently INFLATES the score,
# while a missing one merely falls back to strict matching:
#   - multi-SNP haplotypes (e.g. CYP2B6*6 = rs3745274 + rs2279343),
#   - structural alleles (CYP2D6*5 gene deletion, *1xN/*2xN/*4xN duplications,
#     UGT1A1*28 TA-repeat),
#   - rsIDs that are only a shared *constituent* of several haplotypes.
# Note: an rsID may occur inside several haplotypes (e.g. rs1065852 is in both
# CYP2D6*4 and *10); we map it ONLY to the allele it is the sole DEFINING SNP of
# (*10), so equivalence never crosses into a different haplotype.
# Edit with the same bar: one allele <-> its sole defining rsID.
STAR_ALLELE_DEFINING_RSID = {
    "CYP2C19*2": "rs4244285",
    "CYP2C19*3": "rs4986893",
    "CYP2C19*17": "rs12248560",
    "CYP2C9*2": "rs1799853",
    "CYP2C9*3": "rs1057910",
    "CYP2C9*8": "rs7900194",
    "CYP2D6*3": "rs35742686",
    "CYP2D6*4": "rs3892097",
    "CYP2D6*6": "rs5030655",
    "CYP2D6*9": "rs5030656",
    "CYP2D6*10": "rs1065852",
    "CYP2D6*17": "rs28371706",
    "CYP2D6*41": "rs28371725",
    "CYP4F2*3": "rs2108622",
    "NUDT15*3": "rs116855232",
    "UGT1A1*6": "rs4148323",
    "CYP2B6*9": "rs3745274",
}

# reverse map: defining rsID -> {star alleles it defines}
_RSID_TO_STAR = {}
for _star, _rs in STAR_ALLELE_DEFINING_RSID.items():
    _RSID_TO_STAR.setdefault(_rs, set()).add(_star)


def variant_identity_set(key):
    """All normalized ids that denote the SAME variant as `key`.

    A star allele expands to include its defining rsID; an rsID expands to include
    the star allele(s) it defines. Two keys denote the same variant iff their
    identity sets intersect.
    """
    n = normalize_variant(key)
    ids = {n}
    if n in STAR_ALLELE_DEFINING_RSID:
        ids.add(STAR_ALLELE_DEFINING_RSID[n])
    if n in _RSID_TO_STAR:
        ids |= _RSID_TO_STAR[n]
    return ids


def variant_coverage_match(pred_variants, gold_variants):
    """(matched, total_gold) allowing rsID<->star equivalence.

    Uses max bipartite matching so a single predicted key satisfies AT MOST ONE
    gold key -- otherwise one prediction (e.g. `rs3745274`) could cover two distinct
    gold keys that happen to share a defining SNP (e.g. both `rs3745274` and
    `CYP2B6*9` appear as separate gold keys in PMC4916189).
    """
    gold = list({normalize_variant(v) for v in gold_variants})
    pred = list({normalize_variant(v) for v in pred_variants})
    if not gold:
        return 0, 0
    pred_ids = [variant_identity_set(p) for p in pred]
    adj = [
        [j for j, pid in enumerate(pred_ids) if variant_identity_set(g) & pid]
        for g in gold
    ]
    match_pred = [-1] * len(pred)

    def augment(g, seen):
        for j in adj[g]:
            if not seen[j]:
                seen[j] = True
                if match_pred[j] == -1 or augment(match_pred[j], seen):
                    match_pred[j] = g
                    return True
        return False

    matched = sum(augment(g, [False] * len(pred)) for g in range(len(gold)))
    return matched, len(gold)


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
# Paper-level meaning capture (Recommendation 1) -- representation-invariant
# --------------------------------------------------------------------------- #
def _distinct_sentences(groups):
    """Pool every sentence across a paper's variant keys, deduped, order preserved.

    Collapses gold cross-filing (the same sentence filed under several allele keys)
    back to one distinct meaning, and merges all predicted keys into one pool.
    """
    seen, out = set(), []
    for sents in (groups or {}).values():
        for s in sents or []:
            s = str(s).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def paper_meaning_capture(gold_groups, pred_groups, model):
    """Per-paper recall of gold MEANING, independent of how either side is keyed.

    Pools the paper's distinct gold sentences and its full predicted-sentence set
    (keys ignored), then scores each gold sentence against the whole predicted pool
    with the existing per-gold judge rubric. This removes the penalty for filing a
    correct association under a different-but-valid variant key (granularity,
    cross-filing, *1 reference, rsID-vs-star), while keeping the judge's strictness
    on direction / polarity / phenotype.

    Returns (paper_mean_capture, n_distinct_gold) or (None, 0) when the paper has no
    gold sentences (those papers count only toward coverage).
    """
    gold_sents = _distinct_sentences(gold_groups)
    if not gold_sents:
        return None, 0
    pred_sents = _distinct_sentences(pred_groups)
    per_gold = judge_per_gold(gold_sents, pred_sents, model)
    return (sum(per_gold) / len(per_gold) if per_gold else 0.0), len(gold_sents)


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


def evaluate(generations_dir, judge_model, perkey=True):
    bench = load_bench()
    gold_by_pmcid = {r["pmcid"]: r for r in bench}
    dev, val = split_bench(bench)
    dev_ids = {r["pmcid"] for r in dev}
    gens = load_generations(generations_dir)

    # PRIMARY: paper-level meaning + rsID<->star-aware coverage.
    paper_capture_scores = []
    matched_lenient = total_lenient = 0
    distinct_gold_total = 0
    # SECONDARY (PharmGKB-convention adherence): old strict coverage + per-key macro.
    matched_strict = total_strict = 0
    perkey_macros = []
    captured_gold_equiv = total_gold = 0
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

        # --- PRIMARY coverage: rsID<->star equivalence via bipartite matching. ---
        lmv, ltv = variant_coverage_match(pred_norm.keys(), gold_groups.keys())
        matched_lenient += lmv
        total_lenient += ltv
        # --- SECONDARY coverage: strict representation match. ---
        smv, stv = variant_coverage_counts(pred_norm.keys(), gold_groups.keys())
        matched_strict += smv
        total_strict += stv

        # --- PRIMARY meaning: paper-level, representation-invariant. ---
        paper_capture, n_gold = paper_meaning_capture(
            gold_groups, pred_groups, judge_model
        )
        if paper_capture is not None:
            paper_capture_scores.append(paper_capture)
            distinct_gold_total += n_gold

        # --- SECONDARY meaning: old per-variant macro (convention adherence). ---
        paper_macro = None
        if perkey:
            captures = []
            for v, gold_sents in gold_groups.items():
                if not gold_sents:
                    continue
                pred_sents = pred_norm.get(normalize_variant(v), [])
                capture = judge_sentences(gold_sents, pred_sents, judge_model)
                captures.append(capture)
                captured_gold_equiv += capture * len(gold_sents)
                total_gold += len(gold_sents)
            paper_macro = sum(captures) / len(captures) if captures else 0.0
            perkey_macros.append(paper_macro)

        pc = f"{paper_capture:.3f}" if paper_capture is not None else "n/a"
        pk = f", perkey {paper_macro:.3f}" if paper_macro is not None else ""
        print(
            f"  {pmcid}: coverage {lmv}/{ltv} (strict {smv}/{stv}), "
            f"meaning_capture {pc} over {n_gold} distinct gold sents{pk}",
            file=sys.stderr,
        )

    # PRIMARY metrics.
    meaning_capture = (
        sum(paper_capture_scores) / len(paper_capture_scores)
        if paper_capture_scores
        else 0.0
    )
    variant_coverage = matched_lenient / total_lenient if total_lenient else 0.0
    # SECONDARY metrics.
    variant_coverage_strict = matched_strict / total_strict if total_strict else 0.0
    meaning_capture_perkey = (
        sum(perkey_macros) / len(perkey_macros) if perkey_macros else 0.0
    )
    sentence_coverage = captured_gold_equiv / total_gold if total_gold else 0.0

    if on_dev:
        print(
            f"  NOTE: {on_dev}/{scored} scored papers are DEV papers (val is the honest metric)",
            file=sys.stderr,
        )

    print("---")
    # PRIMARY: representation-invariant meaning + rsID<->star-aware coverage.
    print(f"meaning_capture:         {meaning_capture:.3f}")  # PRIMARY (paper-level)
    print(f"variant_coverage:        {variant_coverage:.3f}")  # PRIMARY (rsID<->star)
    print("--- secondary (PharmGKB-convention adherence) ---")
    if perkey:
        print(f"meaning_capture_perkey:  {meaning_capture_perkey:.3f}")
        print(f"sentence_coverage:       {sentence_coverage:.3f}")  # micro
    print(f"variant_coverage_strict: {variant_coverage_strict:.3f}")
    print("---")
    print(f"num_papers:              {scored}")
    print(f"num_gold_sentences:      {distinct_gold_total}")  # distinct, pooled
    print(f"generations:             {generations_dir}")
    print(f"judge_model:             {judge_model}")
    print(f"total_seconds:           {time.time() - t0:.1f}")


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
    parser.add_argument(
        "--no-perkey",
        action="store_true",
        help="skip the secondary per-variant (convention-adherence) judge calls to "
        "halve judge cost; only the primary paper-level metric is computed",
    )
    args = parser.parse_args()
    evaluate(args.generations, args.judge_model, perkey=not args.no_perkey)


if __name__ == "__main__":
    main()
