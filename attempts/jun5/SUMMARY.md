# autoresearch run — jun5

Branch: `autoresearch/jun5` · judge: **gpt-5.4-mini** · val = 16 papers, 101 gold
sentences. Primary metric: **`meaning_capture`** (macro per-variant batch LLM judge).
**Prior runs are NOT directly comparable.** The committed `eval.py` is byte-identical
to jun4's snapshot, but per the project owner the jun4 (and jun1/jun2) experiments were
actually run against a *different* `eval.py` that was edited before being committed —
so their recorded numbers came from a different harness. jun5 therefore stands entirely
on its own: a fresh baseline and all comparisons computed on the current `eval.py`.

## Result

| | meaning_capture | variant_coverage |
|---|---|---|
| Baseline (minimal single-shot, gpt-5.4) | 0.414 | 0.685 |
| **CHAMPION — iter2 (opus-4-8 + rich PharmGKB prompt)** | **0.558 mean** (0.536 / 0.598 / 0.540 over 3 runs) | ~0.90 |

**Best: iter2 (`8abb30e`)** — net **+0.14 macro** over baseline, validated across three
generations (every champion run beat every non-champion config). (jun4's 0.467 ran on a
different harness and is not comparable.)

## The winning pipeline

A single `litellm` call to **`anthropic/claude-opus-4-8`** with the rich PharmGKB-
convention system prompt (same prompt that did nothing for gpt-5.4):
1. List every variant as a key, including the wild-type `<GENE>*1` reference allele.
2. Allele/diplotype framing for star genes (not `AA`/`GA` genotypes or `PM/IM` labels).
3. Model-side cross-filing — file each diplotype/comparison sentence under every
   constituent allele incl. `*1`.
4. Combine co-reported outcomes into one sentence; no invented reciprocals.

No post-processing, no extra calls. (Mechanics: `litellm.drop_params=True` and omit the
`temperature` param, which opus-4-8 deprecates.)

## What moved the metric — and what didn't

**The decisive lever was the model.** Swapping gpt-5.4 → claude-opus-4-8 on the
identical prompt was **+0.12** (iter2), dwarfing every prompt/structure change (all
within ±0.03–0.05). gemini-2.5-pro (0.503) also far outran gpt-5.4 (0.418); opus edged
gemini.

**But it's an interaction, not the model alone.** The ablation (iter6) is the run's
key finding:

| | minimal prompt | rich PharmGKB prompt |
|---|---|---|
| gpt-5.4  | 0.414 | 0.418 |
| opus-4-8 | 0.409 | **0.536** |

The rich prompt buys opus +0.13 but bought gpt-5.4 ~0. You need **both**: opus can
actually execute the detailed conventions (every variant incl `*1`, cross-filing,
allele framing — coverage 0.71→0.89) that gpt-5.4 received identically and ignored.

**Adding content/compute never helped the macro** (confirmed 4×): extended thinking
(0.458, −0.08), dev-driven rsID/HLA framing rules (0.529, flat), few-shot worked
example (0.495, −0.04), opus+gemini fill-missing ensemble (0.519, flat, **zero**
coverage gain). The batch judge rewards a tight, faithful single-pass per-variant list;
extra sentences dilute the single-sentence variants that dominate the macro. This
reproduces jun4's central lesson on a stronger model.

**Noise is wider than the jun4 estimate.** Identical champion code scored 0.536, 0.598,
0.540 — a 0.062 spread. Treat single-run deltas below ~0.06 as noise. In that light,
iter5/iter7/iter8 are all *within* the champion's band, not real regressions.

## Diagnosis (dev miss analysis, iter5)

A dev-set `diag_misses.py` run on opus showed the dominant remaining miss class is
**rsID variants framed by nucleotide genotype** ("Genotypes CT + TT is associated with
decreased activity of DPYD as compared to genotype CC") and under-enumerated HLA SCAR
alleles. Adding explicit prompt rules for these (iter5) did not move val macro — opus
already handles much of it, and the residual gap is small relative to noise.

## What's left on the table

- The missing ~11% variant coverage is hard for **both** frontier models (the ensemble
  recovered none) — likely genuinely ambiguous/implicit variants, not a model blind spot.
- Mean per-variant capture ≈ 0.56 means ~44% of gold meaning per variant is still
  unrecovered; the levers tried (framing rules, few-shot, more compute) don't close it.
  A genuinely different approach (e.g. retrieval from the PharmGKB variant tables, or a
  judge-aligned rewrite that doesn't add volume) would be the next thing to try.

## Files

Per-iteration `annotation_pipeline.py` + `results.txt` + `notes.md` snapshots are in
`baseline_*/` and `iter*_*/`. `LEARNINGS.md` is the running digest; `results.tsv` is the
full log (untracked). Branch end state = the champion (opus + rich prompt).
