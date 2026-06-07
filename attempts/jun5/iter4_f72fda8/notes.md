# iter4 (f72fda8) — opus-4-8 + extended thinking (reasoning_effort=high)

**Hypothesis:** letting opus reason (extended thinking) before emitting sentences
would improve faithful framing of direction/polarity/comparison — the capture
axis the judge rewards.

**Changed vs champion (iter2):** added `reasoning_effort="high"` for opus. Nothing
else.

**Numbers (val, 16 papers):**
- meaning_capture: **0.458** (plain opus 0.536) — **−0.078, real regression**
- variant_coverage: 0.876 (≈ 0.888)
- sentence_coverage: 0.400 (opus 0.513) — −0.113

**Effect:** worse.

**Lesson:** extended thinking HURTS here. Coverage held but micro/macro both fell —
thinking made opus emit fewer / less gold-matching sentences per variant (more
conservative, restructured framing away from the gold's literal PharmGKB phrasing).
The task rewards opus's direct single-pass extraction, not deliberation. **Keep
plain opus, no reasoning.** Confirms jun4 lesson generalized: "more compute/content"
(2-pass, capping, now thinking) does not help the per-variant capture macro.
