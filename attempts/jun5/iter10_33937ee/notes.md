# iter10 (33937ee) — champion VALIDATION re-run #2 (identical to iter2)

**Hypothesis:** third sample of the champion config (opus-4-8 + rich prompt) to pin
its mean and noise band.

**Changed vs champion:** nothing — same code.

**Numbers (val, 16 papers):**
- meaning_capture: **0.540** (iter2 0.536, iter9 0.598)
- variant_coverage: 0.899
- sentence_coverage: 0.510

**Effect:** re-run (no change).

**Lesson — champion pinned.** Three identical-config runs: **0.536 / 0.598 / 0.540
→ mean 0.558**, range [0.536, 0.598], sd ≈ 0.034. The champion (opus + rich PharmGKB
prompt) is **≈0.56 ± 0.03**, a solid **+0.14 over baseline (0.414)** and above every
other config tried. Confirms ~±0.03–0.05 single-run noise. End of jun5 loop.
