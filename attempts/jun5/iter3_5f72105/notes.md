# iter3 (5f72105) — gemini-2.5-pro as pipeline model

**Hypothesis:** if model quality is the lever (iter2), another frontier model might
match or beat opus-4-8. Swap MODEL to `gemini/gemini-2.5-pro`, same iter1 prompt.

**Changed vs champion (iter2):** only `MODEL` (opus-4-8 → gemini-2.5-pro).

**Numbers (val, 16 papers):**
- meaning_capture: **0.503** (opus 0.536, gpt-5.4 0.418)
- variant_coverage: 0.865
- sentence_coverage: 0.499

**Effect:** worse than champion (−0.033, ~noise-floor) but far above gpt-5.4.

**Lesson:** confirms the model-quality finding — BOTH frontier models (opus 0.536,
gemini 0.503) hugely outperform gpt-5.4 (0.418) on identical prompt. opus edges
gemini by ~one noise unit; keep **opus-4-8 as champion**. The task is model-bound:
the gap between frontier and gpt-5.4 (~+0.12) dwarfs any prompt tweak seen so far
(~±0.03). Next: push opus further (prompt rubric, extended thinking), not more model
swaps.
