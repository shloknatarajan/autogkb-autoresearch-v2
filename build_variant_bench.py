"""
One-time builder for the per-variant sentence benchmark.

Transforms the flat `benchmarks/sentence_bench_collapsed.jsonl` (each paper is a
flat `variants` list + flat `sentences` list) into the per-variant structure
`benchmarks/sentence_bench_by_variant.jsonl`, where each paper's sentences are
grouped under the variant they assert an association about:

    { "pmcid", "pmid",
      "variant_sentences": { "<variant>": ["sentence", ...], ... },
      "markdown_content": "..." }

The grouping is recovered deterministically from the raw PharmGKB tables in
`base_data/variantAnnotations/*.tsv`: every standardized sentence there carries a
`Variant/Haplotypes` column. We match each collapsed sentence back to its source
row (by PMID + fuzzy sentence similarity, since the collapsed form inserts a study
population phrase) and file it under every *declared* variant that row names.
An LLM fallback assigns any sentence that fails to match a source row.

Run once:  uv run build_variant_bench.py
"""

import csv
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).parent
COLLAPSED = HERE / "benchmarks" / "sentence_bench_collapsed.jsonl"
OUT = HERE / "benchmarks" / "sentence_bench_by_variant.jsonl"
TSV_DIR = HERE / "base_data" / "variantAnnotations"
TSV_FILES = ["var_drug_ann.tsv", "var_pheno_ann.tsv", "var_fa_ann.tsv"]

MATCH_THRESHOLD = 0.6  # SequenceMatcher ratio below which we fall back to the LLM
FALLBACK_MODEL = "gpt-5.4-mini"


def normalize_variant(v):
    """Mirror eval.normalize_variant: canonical rsID / uppercased no-space form."""
    s = str(v).strip()
    m = re.fullmatch(r"[rR][sS]\s*0*(\d+)", s)
    if m:
        return "rs" + m.group(1)
    return re.sub(r"\s+", "", s).upper()


def _core(s):
    return re.sub(r"\s+", " ", s.lower()).strip()


def load_tsv_rows_by_pmid():
    """{pmid: [(variant_haplotypes_raw, sentence), ...]} across all three tables."""
    rows = {}
    for name in TSV_FILES:
        with open(TSV_DIR / name, encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                pmid = (row.get("PMID") or "").strip()
                if not pmid:
                    continue
                rows.setdefault(pmid, []).append(
                    (
                        row.get("Variant/Haplotypes", "") or "",
                        row.get("Sentence", "") or "",
                    )
                )
    return rows


def variant_tokens(variant_haplotypes):
    """Split a `Variant/Haplotypes` cell ('CYP2C19*1, CYP2C19*2') into normalized ids."""
    parts = re.split(r"[;,]| and | & ", variant_haplotypes)
    return [normalize_variant(p) for p in parts if p.strip()]


def best_tsv_match(sentence, tsv_rows):
    """Return (variant_haplotypes, ratio) of the best-matching source row, or (None, 0)."""
    cs = _core(sentence)
    best_ratio, best_vh = 0.0, None
    for vh, ts in tsv_rows:
        ratio = SequenceMatcher(None, cs, _core(ts)).ratio()
        if ratio > best_ratio:
            best_ratio, best_vh = ratio, vh
    return best_vh, best_ratio


def llm_assign_variant(sentence, declared_variants):
    """Fallback: ask an LLM which declared variant(s) a sentence is about."""
    import litellm

    prompt = (
        "A pharmacogenomics 'standardized association sentence' is given, along with "
        "the list of variants discussed in its paper. Return JSON "
        '{"variants": ["..."]} listing exactly the variant id(s) from the provided '
        "list that this sentence asserts an association about (usually one).\n\n"
        f"Variants: {declared_variants}\n\nSentence: {sentence}"
    )
    resp = litellm.completion(
        model=FALLBACK_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content or "{}"
    try:
        picked = json.loads(text).get("variants", []) or []
    except json.JSONDecodeError:
        picked = []
    declared_norm = {normalize_variant(v): v for v in declared_variants}
    return [
        declared_norm[normalize_variant(p)]
        for p in picked
        if normalize_variant(p) in declared_norm
    ]


def build():
    tsv_by_pmid = load_tsv_rows_by_pmid()
    bench = [json.loads(ln) for ln in COLLAPSED.read_text().splitlines() if ln.strip()]

    out_records = []
    n_matched = n_fallback = n_orphan = 0
    for r in bench:
        pmid = str(r.get("pmid", "")).strip()
        declared = list(r["variants"])
        declared_norm = {normalize_variant(v): v for v in declared}
        tsv_rows = tsv_by_pmid.get(pmid, [])

        # Preserve declared-variant order; every declared variant is a key (possibly empty).
        groups = {v: [] for v in declared}

        for sentence in r["sentences"]:
            vh, ratio = best_tsv_match(sentence, tsv_rows)
            if vh is not None and ratio >= MATCH_THRESHOLD:
                targets = [
                    declared_norm[t] for t in variant_tokens(vh) if t in declared_norm
                ]
                n_matched += 1
            else:
                targets = llm_assign_variant(sentence, declared)
                n_fallback += 1
            if not targets:
                n_orphan += 1
                print(
                    f"  {r['pmcid']}: WARNING no declared variant for sentence: {sentence[:80]!r}",
                    file=sys.stderr,
                )
                continue
            for v in targets:
                if sentence not in groups[v]:
                    groups[v].append(sentence)

        out_records.append(
            {
                "pmcid": r["pmcid"],
                "pmid": r.get("pmid"),
                "variant_sentences": groups,
                "markdown_content": r.get("markdown_content", ""),
            }
        )

    OUT.write_text("\n".join(json.dumps(rec) for rec in out_records) + "\n")
    total_sent = sum(len(r["sentences"]) for r in bench)
    print(
        f"wrote {len(out_records)} papers to {OUT.relative_to(HERE)} "
        f"(sentences: {total_sent}, tsv-matched: {n_matched}, llm-fallback: {n_fallback}, "
        f"orphaned: {n_orphan})"
    )


if __name__ == "__main__":
    build()
