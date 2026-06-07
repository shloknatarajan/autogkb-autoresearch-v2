# iter7 (b086029) — few-shot worked output mapping on opus

**Hypothesis:** a concrete worked example (a full `variant_sentences` mapping showing
star-allele cross-filing under *1/*6/*28, rsID genotype framing with comparison, and
a null `[]` variant) would anchor opus's framing better than the abstract conventions.

**Changed vs champion (iter2):** replaced the two standalone example sentences with a
compact worked output mapping (drawn from dev gold, different paper). Same model/prompt
otherwise.

**Numbers (val, 16 papers):**
- meaning_capture: **0.495** (champion 0.536) — −0.041, slightly past noise
- variant_coverage: 0.876 (0.888)
- sentence_coverage: 0.474 (0.513)

**Effect:** worse.

**Lesson:** the worked example did NOT help and slightly hurt — a concrete exemplar
appears to narrow opus's output distribution (anchoring on the example's structure)
vs the abstract conventions which let opus generalize per paper. Consistent with the
run's theme: opus + the abstract rich prompt (iter2) is the sweet spot; extra
constraints/content (thinking, rsID/HLA rules, few-shot) stay at-or-below it. Champion
stays iter2.
