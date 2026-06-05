# jun5 — LEARNINGS (running digest)

Run under the batch-judge harness (one judge call per gold variant → fraction of
that variant's gold meaning captured, macro-averaged over variants then papers).
Pipeline model gpt-5.4, judge gpt-5.4-mini, val = 16 papers. Primary metric:
**meaning_capture** (higher is better).

Format: `<label> <meaning_capture>/<variant_coverage> <effect> — lesson`

- **baseline_ff81a57** 0.414/0.685 baseline — minimal single-shot gpt-5.4. Floor ≈ 0.41. Matches jun4 baseline re-run (0.412) → current eval.py is byte-identical to jun4's; jun4 lessons apply, jun1/jun2 (diff harness) do not. Misses ~31% of gold variant keys (esp. `<GENE>*1`), frames by genotype/metabolizer not star-allele diplotype.

