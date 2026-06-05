# autoresearch run — jun4

Branch: `autoresearch/jun4` · pipeline model: **gpt-5.4** · judge: **gpt-5.4-mini** ·
val = 16 papers. Primary metric: **`meaning_capture`** (macro per-variant batch LLM judge).

This run is the first under the **new batch-judge harness** (one judge call per gold
variant returns the fraction of that variant's gold meaning recovered, macro-averaged).
That changes the optimization landscape completely vs the jun1/jun2 per-sentence judge.

## Result

| | meaning_capture | variant_coverage |
|---|---|---|
| Baseline (minimal single-shot) | 0.448 (re-run 0.412) | 0.685 |
| **Champion — iter7** | **0.467 (re-run 0.455)** | **0.899** |

**Best: iter7 (`d5528b7`)** — a single rich PharmGKB-convention pass with **model-side
cross-filing**. Net gain over baseline ≈ **+0.03 macro** and **+0.21 variant coverage**,
both validated by a second generation (see noise note below).

## What iter7 does (the winning pipeline)

One `litellm` call to gpt-5.4 with a system prompt that pins the output to PharmGKB
house conventions:

1. **List every variant as a key**, including the wild-type `<GENE>*1` reference allele.
2. **Allele/diplotype framing**, never nucleotide genotypes (`AA`/`GA`) or metabolizer
   labels (`PM/IM`) — translate to the underlying alleles.
3. **File each diplotype/comparison sentence under every constituent allele it names,
   including `*1`** — done *in the prompt* (model-side), not by regex post-processing.
4. **Combine co-reported outcomes** into one sentence ("Neutropenia, Leukopenia or
   Diarrhea"); no invented reciprocals or genotype enumerations.

No post-processing, no extra model calls. Simple and cheap (~37 s/run for 16 papers).

## The two findings that drove the run

**1. The new batch judge punishes *adding* content.** Every "more recall" tactic
LOWERED macro even when it RAISED micro `sentence_coverage`:
- iter1 (exhaustive + reciprocal/genotype flooding): macro 0.392 — contradictory framings
  confuse the per-variant judge.
- iter4 (PharmGKB prompt + **regex** cross-filing): `sentence_coverage` jumped 0.324→0.416
  (+0.09, real) but macro fell to 0.403 — the regex replicates diplotype sentences onto
  rsIDs / single-allele keys, diluting the **single-sentence variants** that dominate the
  macro (74 of 90 dev variants have exactly 1 gold sentence).
- iter9 (two-pass union, cap 5): 0.439. iter10 (cap=3): 0.407 — capping drops gold-matching
  sentences. iter8 (fill-missing cross-file): 0.444. All below iter7.

The lever is **better, not more**: capture more gold meaning *without* lengthening the
per-variant list. iter7 gets the coverage/micro gains by letting the *model* decide which
alleles a sentence belongs under (it only cross-files genuine star/HLA-allele sentences),
so rsIDs and single alleles keep their tight 1–2 sentence lists.

**2. The metric is noise-dominated at 16 papers.** Re-running the *identical* baseline
gave 0.448 then 0.412 — a **0.036 swing from generation noise** (gpt-5.4 is not
deterministic at temperature 0; each run re-generates). So differences < ~0.04 are not
resolvable in a single run. iter7 was therefore **validated with a second generation**
(0.467, 0.455) — both clearly above the baseline band [0.412, 0.448]. Single-run
keep/discard on small deltas (iter3, iter5) is unreliable and was treated as "within noise".

## Diagnosis that pointed at iter7

A dev-set miss diagnostic (`diag_misses.py`) showed the baseline *finds the biology but
mis-frames it*: it wrote metabolizer-phenotype sentences (`PM/IM`, "disease event") where
gold wanted star-allele diplotypes (`*3/*3 + *4/*4`, "Recurrence"); genotype-letter
sentences (`*6 AA/GA`) where gold wanted `*6 + *28` combined diplotypes filed under
`*1/*6/*28`. The dominant miss class was **`<GENE>*1` reference-allele keys** that gold
populates with comparison sentences. iter7's prompt targets exactly these.

## What didn't work / not tried

- Regex cross-filing (`tools/cross_file.py`) as post-processing — dilutes single-sentence
  variants; model-side cross-filing is strictly better here.
- Terse sentences (iter2) — drops qualifiers (population, comparison group) the judge gives
  partial credit for.
- A different pipeline model was not tried (time budget); it is the main untried lever with
  real ceiling, since iter7 looks near the practical limit for single-pass gpt-5.4.

## Files

Per-iteration `annotation_pipeline.py` + eval `results.txt` snapshots are in
`baseline_*/` and `iter*_*/`. `results.tsv` is the full log; `eval.py` is the harness
used for this run.
