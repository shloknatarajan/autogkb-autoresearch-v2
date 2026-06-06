# iter9 (4601d16) — champion VALIDATION re-run #1 (identical to iter2)

**Hypothesis:** with the idea space exhausted (all levers ≤ opus+rich-prompt), use
the final iterations to validate the champion's true score by re-running the
IDENTICAL config (opus-4-8 + rich prompt, no code change vs iter2).

**Changed vs champion (iter2):** nothing — same code. This is a noise-band probe.

**Numbers (val, 16 papers):**
- meaning_capture: **0.598** (iter2 same-config run: 0.536) — **+0.062 swing on identical code**
- variant_coverage: 0.910 (0.888)
- sentence_coverage: 0.539 (0.513)

**Effect:** re-run (no change). Nominally above prior best but it's the SAME config.

**Lesson — recalibrates the noise floor.** The identical champion config scored
0.536 then 0.598: a **0.062 generation+judge swing**. So the opus noise band is
WIDER (~±0.05) than the gpt-5.4-era ±0.03 estimate. Consequence: iter5 (0.529),
iter7 (0.495), iter8 (0.519) all sit WITHIN the champion's noise band — they are not
real regressions, just variants of the champion that didn't clearly help. The only
configs clearly OUTSIDE (worse) are: gpt-5.4 any-prompt (~0.41), opus+minimal-prompt
(0.409), opus+extended-thinking (0.458), and gemini (0.503, borderline). The champion
config (opus + rich prompt) is unambiguously the best family; its mean is ~0.55–0.57.
