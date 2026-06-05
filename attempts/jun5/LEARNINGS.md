# jun5 — LEARNINGS (running digest)

Run under the batch-judge harness (one judge call per gold variant → fraction of
that variant's gold meaning captured, macro-averaged over variants then papers).
Pipeline model gpt-5.4, judge gpt-5.4-mini, val = 16 papers. Primary metric:
**meaning_capture** (higher is better).

Format: `<label> <meaning_capture>/<variant_coverage> <effect> — lesson`

