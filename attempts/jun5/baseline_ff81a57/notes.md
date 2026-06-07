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

**Notes / harness check:** The committed `eval.py` is byte-identical to the
`attempts/jun4/eval.py` snapshot, BUT per the project owner the jun4 (and jun1/jun2)
experiments were actually run against a different `eval.py` that was edited before
being committed — so their recorded numbers came from a different harness and are
NOT comparable to jun5 (the 0.414≈jun4's 0.412 match is coincidence). jun5 stands on
its own fresh baseline; every candidate is judged only against jun5 runs on the
current `eval.py`.

**Lesson:** floor ≈ 0.41–0.45 for single-shot gpt-5.4. The model finds variants
it mentions but misses ~31% of gold variant keys (notably `<GENE>*1` reference
keys), and frames by genotype/metabolizer rather than star-allele diplotype — the
miss classes jun4 diagnosed. Those are the levers to pull.
