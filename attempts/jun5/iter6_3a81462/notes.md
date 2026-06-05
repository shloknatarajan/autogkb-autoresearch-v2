# iter6 (3a81462) — ABLATION: opus + minimal baseline prompt

**Hypothesis:** is the champion (iter2) carried by opus, or by the rich PharmGKB
prompt? Run opus with the *minimal* baseline prompt (the one gpt-5.4 baseline used).
If ≈0.536, the prompt doesn't matter and we could simplify.

**Changed vs champion (iter2):** SYSTEM_PROMPT reverted to the minimal baseline
prompt. Same opus model.

**Numbers (val, 16 papers):**
- meaning_capture: **0.409** (champion 0.536) — **−0.127, big drop**
- variant_coverage: 0.708 (0.888) — −0.18
- sentence_coverage: 0.351 (0.513)

**Effect:** worse (informative ablation).

**Lesson — the key finding of the run.** The result is an INTERACTION, not model-
alone or prompt-alone:

| | minimal prompt | rich PharmGKB prompt |
|---|---|---|
| gpt-5.4  | 0.414 (baseline) | 0.418 (iter1) |
| opus-4-8 | 0.409 (this)     | **0.536 (iter2)** |

The rich prompt buys **opus +0.13** but bought **gpt-5.4 ~nothing**. Opus can
actually EXECUTE the detailed conventions (list every variant incl `<GENE>*1`,
model-side cross-filing, allele/diplotype framing, combine outcomes) — coverage
0.708→0.888 — and that execution is what lifts macro. gpt-5.4 got the same
instructions and couldn't capitalize. **So the champion needs BOTH: a capable model
AND the rich PharmGKB prompt.** Corrects iter3's "model-bound not prompt-bound"
note — it's the combination. Champion stays iter2.
