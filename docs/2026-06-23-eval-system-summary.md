# Eval System Summary — 2026-06-23

A concise overview of the revised `eval.py` scoring system. For the *why* see
[ANNOTATION_AMBIGUITY.md](ANNOTATION_AMBIGUITY.md); for the validation see
[METRIC_VALIDATION.md](METRIC_VALIDATION.md).

## What changed and why

The harness was revised to measure **extraction skill** (did you recover the correct
associations?) rather than **PharmGKB filing convention** (did you key and cross-file them
the way PharmGKB happened to?). The old per-variant metric conflated the two: a correct
association filed under a different-but-valid key scored as a miss, and PharmGKB's habit of
cross-filing one sentence under every constituent allele meant a single biological finding
was charged as many separate obligations (e.g. PMC6435416 — one association cross-filed
under 15 CYP2D6 allele keys).

## The metrics

Both metrics are **recall only** — extra predicted items are never penalized.

### Primary (representation-invariant — the headline)

- **`meaning_capture`** — **paper-level**. All of a paper's *distinct* gold sentences are
  pooled (collapsing cross-filing) and scored against the pipeline's *entire* predicted
  sentence pool, with variant keys **ignored**. Each gold sentence gets a 0–1 capture score
  from the LLM judge; averaged per paper, then macro-averaged across papers. The judge stays
  **strict on direction / polarity / phenotype** (*increased* ≠ *decreased*, *is* ≠ *is not
  associated*). → `paper_meaning_capture()`.
- **`variant_coverage`** — recall over gold variant keys, accepting **rsID ↔ star-allele
  equivalence**. A star allele matches its defining rsID and vice versa, via a curated
  single-defining-SNP table (`STAR_ALLELE_DEFINING_RSID`) and **bipartite matching** so one
  prediction can satisfy at most one gold key. → `variant_coverage_match()`.

### Secondary (PharmGKB-convention adherence — kept for comparison)

- **`meaning_capture_perkey`** — the old per-variant macro (one judge call per gold variant,
  sentences matched only under the same normalized key).
- **`variant_coverage_strict`** — old exact-representation key match.
- **`sentence_coverage`** — micro-average, informational.

`--no-perkey` skips the per-variant judge calls (halves judge cost; primary metrics only).

## The rsID ↔ star-allele table

`STAR_ALLELE_DEFINING_RSID` is **curated, conservative, and auditable**: only star alleles
fixed by a *single* canonical defining SNP (verified against PharmVar/CPIC). Deliberately
omitted — because a wrong equivalence silently *inflates* the score while a missing one only
falls back to strict matching: multi-SNP haplotypes (CYP2B6\*6), structural alleles
(CYP2D6\*5 deletion, \*xN duplications, UGT1A1\*28 TA-repeat), and rsIDs that are only a
shared constituent of several haplotypes. Bipartite matching prevents a single prediction
from collapsing two distinct gold keys that share a defining SNP (e.g. `CYP2B6*9` and
`rs3745274` both appear as separate gold keys in PMC4916189).

## Output shape

```
---
meaning_capture:         0.548   # PRIMARY — paper-level, representation-invariant
variant_coverage:        0.906   # PRIMARY — rsID<->star equivalence
--- secondary (PharmGKB-convention adherence) ---
meaning_capture_perkey:  0.536
sentence_coverage:       0.509
variant_coverage_strict: 0.899
---
num_papers:              16
num_gold_sentences:      52      # DISTINCT, pooled (was ~101 cross-filed under old metric)
```

## Validation results (champion, val=16)

Re-baselined by re-scoring three clean champion-era opus generations (regeneration was
blocked at the time by an exhausted Anthropic credit balance; the decoupled
generation/scoring design made re-scoring possible):

| | meaning_capture (NEW) | per-key (OLD) | coverage (NEW) | strict (OLD) |
|---|---|---|---|---|
| clean champion, mean of 3 | **0.548** | 0.536 | 0.906 | 0.899 |

**Verdict: a genuine improvement.** On a system that already follows PharmGKB conventions
the new and old scores agree in level (and the new metric was no noisier), so it's a safe
drop-in — while it correctly credits biology the old metric wrongly zeroed (PMC6435416:
0.09 → 1.00) and the synthetic misfiled-key test scores 1.000 (new) vs 0.000 (old).

## Caveats to keep in mind

- **Volume sensitivity.** Paper-level pooling hands the judge the whole prediction set, so a
  *flooding* pipeline (hundreds of sentences) can drown gold meanings and score low by judge
  distractor-confusion (observed: a 155-sentence pool scored a present meaning 0.00). Clean
  systems (pools ~8) are unaffected; if a pipeline over-generates, read `meaning_capture`
  alongside pool size.
- **Judge noise ~±0.05**, worse on papers with only 1–2 distinct gold sentences (near-binary).
  Always re-baseline a champion over **≥3 generations**.
- **Rubric confound.** The primary uses the per-gold diagnostic judge; the secondary uses the
  aggregate judge. Levels agree, so the rubrics are roughly calibrated, but per-paper
  divergence is partly rubric, not just keying.
- **Not comparable to jun1..jun5** — the primary metric's definition changed.

## Still open (from ANNOTATION_AMBIGUITY.md)

Exclude/separately-report `*1` reference-allele keys; synonym-aware phenotype matching; audit
gold items unrecoverable from `markdown_content` (supplements); standardize mean±std over ≥3
generations as the reporting unit.
