# iter1 (a1c8201) — PharmGKB conventions + model-side cross-filing

**Hypothesis:** reproduce jun4's champion mechanism (iter7) on jun5's identical
harness — rich PharmGKB-convention prompt that tells the *model* to (a) list every
variant incl. `<GENE>*1`, (b) frame by star-allele/diplotype not genotype/metabolizer,
(c) file each diplotype/comparison sentence under every constituent allele, (d)
combine co-reported outcomes. Expect ~+0.03 macro and big coverage gain.

**Changed vs best-so-far (baseline):** swapped the minimal prompt for the full
iter7 PharmGKB-convention prompt. No code/post-processing change.

**Numbers (val, 16 papers):**
- meaning_capture: **0.418** (baseline 0.414) — FLAT, within ±0.03 noise
- variant_coverage: 0.888 (baseline 0.685) — **+0.20**
- sentence_coverage: 0.421 (baseline 0.325) — **+0.10**

**Effect:** similar (macro within noise; coverage much better).

**Lesson:** The coverage/micro half of jun4 iter7 reproduces exactly, but the macro
gain does NOT — 0.418 sits right on the baseline. So on this harness the mechanism
buys variant coverage + micro recall, not macro meaning_capture. (jun4's headline
0.467 ran on a different eval.py and is not a comparable reference.) The pattern
matches jun4's qualitative lesson #1 anyway — adding content moves micro/coverage but
not the macro, which is dominated by the single-sentence variants. **The real lever
for macro is per-variant capture QUALITY, not
coverage.** Build forward on iter1 anyway: same macro as baseline but +0.20 coverage
for free is a strictly better base. Next: improve how faithfully each variant's
sentences match gold meaning (direction/polarity/comparison/qualifiers), not volume.
