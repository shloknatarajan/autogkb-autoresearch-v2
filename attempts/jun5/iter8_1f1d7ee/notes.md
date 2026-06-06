# iter8 (1f1d7ee) — ensemble: opus primary + gemini fill-missing variants

**Hypothesis:** the champion misses ~11% of gold variants (coverage 0.888). Have a
second frontier model (gemini-2.5-pro) extract the same paper and ADD only the
variants opus missed entirely — never touching opus's per-variant lists — to close
the coverage gap without dilution. Missed variants score 0, so genuine adds should
help macro.

**Changed vs champion (iter2):** added a second `_call` to gemini and a fill-missing
merge keyed by a loose canonical id. 2x model calls.

**Numbers (val, 16 papers):**
- meaning_capture: **0.519** (champion 0.536) — −0.017, within noise
- variant_coverage: 0.888 (= opus alone) — **NO gain**
- sentence_coverage: 0.474 (0.513) — −0.04

**Effect:** similar macro, but no benefit + 2x cost + added complexity → rejected.

**Lesson:** the fill-missing ensemble produced **zero coverage gain** — gemini did
not recover any GOLD variant opus missed, so the missing ~11% are genuinely hard for
both frontier models (not a model-specific blind spot). The gemini-only variants it
DID add don't match gold keys (no coverage credit) and the extra content slightly
dilutes micro. Confirms, a 4th time (after 2-pass, thinking, regex cross-file), that
**adding content does not raise this macro**. The single-pass opus+rich-prompt
champion is the ceiling for the approaches tried. Champion stays iter2.
