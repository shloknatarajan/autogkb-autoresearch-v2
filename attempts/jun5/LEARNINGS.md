# jun5 — LEARNINGS (running digest)

Run under the batch-judge harness (one judge call per gold variant → fraction of
that variant's gold meaning captured, macro-averaged over variants then papers).
Pipeline model gpt-5.4, judge gpt-5.4-mini, val = 16 papers. Primary metric:
**meaning_capture** (higher is better).

Format: `<label> <meaning_capture>/<variant_coverage> <effect> — lesson`

- **baseline_ff81a57** 0.414/0.685 baseline — minimal single-shot gpt-5.4. Floor ≈ 0.41. Matches jun4 baseline re-run (0.412) → current eval.py is byte-identical to jun4's; jun4 lessons apply, jun1/jun2 (diff harness) do not. Misses ~31% of gold variant keys (esp. `<GENE>*1`), frames by genotype/metabolizer not star-allele diplotype.
- **iter1_a1c8201** 0.418/0.888 similar — PharmGKB conventions + model-side cross-filing (jun4 iter7 mechanism). Coverage +0.20, micro +0.10, but **macro FLAT** (0.418 vs 0.414). jun4's headline 0.467 was generation noise; the mechanism buys coverage/micro, NOT macro. **Macro lever = per-variant capture quality, not volume.** Building forward on iter1 (free +0.20 coverage).

