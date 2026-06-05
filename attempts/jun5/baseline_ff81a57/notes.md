# baseline (ff81a57)

**Hypothesis:** establish an honest floor — one straightforward single-shot
`litellm` call to gpt-5.4 asking for the `{variant -> [sentences]}` mapping as JSON.

**What it does:** minimal PharmGKB-curator system prompt (canonical keys, one
sentence-style example), `temperature=0`, `response_format=json_object`. No
decomposition, no cross-filing, no post-processing.

**Numbers (val, 16 papers):**
- meaning_capture: **0.414**
- variant_coverage: 0.685
- sentence_coverage: 0.325

**Effect:** baseline.

**Notes / harness check:** Current `eval.py` is byte-identical to
`attempts/jun4/eval.py` (same batch-judge harness). This baseline's 0.414 matches
jun4's baseline re-run (0.412) almost exactly — confirms the harness and the
~±0.03 generation-noise floor are unchanged. jun1/jun2 used a *different* eval
(per-sentence judge) so their numbers are NOT comparable; jun4's ARE, but every
candidate will still be re-validated on jun5's own runs given the noise.

**Lesson:** floor ≈ 0.41–0.45 for single-shot gpt-5.4. The model finds variants
it mentions but misses ~31% of gold variant keys (notably `<GENE>*1` reference
keys), and frames by genotype/metabolizer rather than star-allele diplotype — the
miss classes jun4 diagnosed. Those are the levers to pull.
