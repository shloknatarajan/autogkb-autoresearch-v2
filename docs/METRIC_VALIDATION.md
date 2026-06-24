# Metric Revision — Validation & Verdict (2026-06-23)

Re-baseline and direct failure analysis of the revised `eval.py` (paper-level
`meaning_capture` + rsID↔star `variant_coverage`), to judge whether it is a genuine
improvement over the per-key metric. See `ANNOTATION_AMBIGUITY.md` for the rationale.

## Method note (and a finding in itself)

The opus champion could **not** be regenerated — the Anthropic API credit balance is
exhausted (`generate.py` 400s on every opus call). Because generation and scoring are
decoupled, I re-baselined by **re-scoring existing champion-era val generations** with
the new harness (OpenAI judge still works).

Selecting candidates by coverage alone accidentally picked up an **over-generation
experiment** (`20260603-040409`: median pool **73** sentences, max **202**) instead of
the clean champion (median pool ~8–10). That contamination is itself informative (below),
but the clean re-baseline uses three tight-pool, high-coverage val runs.

## Clean champion re-baseline (3 runs, opus + rich prompt, val=16)

| generation | pred-pool median | meaning_capture (NEW) | meaning_capture_perkey (OLD) | variant_coverage (NEW) | strict (OLD) |
|---|---|---|---|---|---|
| 20260606-001012 | 10 | 0.590 | 0.599 | 0.910 | 0.910 |
| 20260605-224751 | 8  | 0.533 | 0.538 | 0.910 | 0.888 |
| 20260604-055615 | 9  | 0.522 | 0.471 | 0.899 | 0.899 |
| **mean** | | **0.548** | **0.536** | **0.906** | **0.899** |
| range | | 0.068 | 0.128 | | |

**On the clean champion the new and old metrics agree in level (0.548 vs 0.536)** and the
new metric is *not* noisier (range 0.068 vs 0.128 — the old per-key macro actually swung
more here). So the revision is a safe drop-in: it does not inflate or destabilize the
score of a system that already follows PharmGKB conventions.

## Where the metrics diverge — direct failure analysis

### The fix working (new is MORE correct) — heavy cross-filing
- **PMC6435416**: ONE association (poor/intermediate vs normal/ultrarapid CYP2D6
  metabolizers) that PharmGKB cross-files under **15 allele keys**. Old per-key scored the
  agent **0.09–0.62** for not replicating the identical sentence under all 15 keys; new
  paper-level sees **1 distinct meaning, captured → 1.00**. Unambiguously fairer.
- **PMC11430164** (18 keys, 2 distinct meanings) and **PMC10399933** show the same pattern:
  the new metric stops charging the agent once per cross-filed key.

### The rsID↔star equivalence working
- **PMC10399933** (`20260605-224751`): lenient coverage **4/5** vs strict **2/5** — the
  equivalence recovered 2 CYP2C9 variants the agent reported in the other valid
  representation. Net effect across the clean champion: coverage 0.906 vs 0.899 (small but
  real; most champion predictions already use the gold's representation, so the equivalence
  is a safety net, not a windfall).

### The one real weakness — volume sensitivity (new can be HARSHER, by judge confusion)
- **PMC10993165** (flooder run): 3 gold HLA-B meanings, **155** predicted sentences.
  Old per-key = 0.97 (it scopes the judge to each variant key); new paper-level = 0.00 —
  the per-gold judge, handed 3 needles in 155 distractors, fails to credit them. The agent
  *did* produce the right variants under the right keys, so this is partly a judge-capacity
  artifact, not pure correctness.
  - This **only bites flooders** (the over-generation experiment scored new 0.470 < old
    0.528). On clean champions (pools ~8) it does not appear. It is arguably a *feature*
    (discourages dumping 155 sentences to cover 3), but it is an unprincipled, noisy
    penalty rather than a clean precision term, and it sits in tension with the stated
    recall-only philosophy.

### Confound to keep in mind
The new primary uses the **per-gold diagnostic judge** (`judge_per_gold`, scores each gold
sentence individually); the old per-key uses the **aggregate judge** (`judge_sentences`,
one score per variant). The level agreement (0.548 vs 0.536) shows the two rubrics are
roughly calibrated, but part of any per-paper divergence is the rubric, not just the keying.

## Verdict: **a genuine improvement, with one caveat to watch**

1. **Representation-invariance is real and correct.** The decisive cases (PMC6435416's
   15-keys-one-meaning; the misfiled-key test scoring 1.000 vs 0.000) show the new metric
   credits correct biology the old one wrongly zeroed for filing convention. This is the
   stated goal, achieved.
2. **No cost on the thing we optimize.** On the clean champion the score and its stability
   are unchanged, so switching the loop's target metric loses nothing.
3. **Coverage is fairer** via rsID↔star equivalence, conservatively (bipartite, curated
   single-SNP table), with a verified real recovery and no observed false merges.
4. **Caveat:** paper-level pooling reintroduces a *volume sensitivity* through judge
   distractor-confusion on huge prediction pools. It doesn't affect clean systems, and
   jun4/jun5 already established that flooding hurts — but if a future pipeline over-
   generates, interpret a low new-`meaning_capture` alongside pool size, and consider
   capping the pool handed to the judge (e.g. top-K by relevance) if it becomes load-bearing.

Net: keep the revision as the primary metric. The remaining open items from
`ANNOTATION_AMBIGUITY.md` (exclude `*1` reference keys; synonym-aware phenotypes; audit
beyond-markdown gold; report mean±std over ≥3 generations) are still worth doing, and the
small-N judge noise (~±0.05) is unchanged — re-baseline with ≥3 runs remains mandatory.
