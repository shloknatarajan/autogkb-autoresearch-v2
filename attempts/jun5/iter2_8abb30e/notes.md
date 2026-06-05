# iter2 (8abb30e) — swap pipeline model to claude-opus-4-8

**Hypothesis:** jun4's one untried lever with real ceiling was a stronger/different
pipeline model. Keep iter1's PharmGKB-convention prompt; swap the pipeline model
from gpt-5.4 to **anthropic/claude-opus-4-8** (also independent from the OpenAI
judge, so no same-model bias).

**Changed vs best-so-far (iter1):** only `MODEL`. Plus two mechanical fixes so the
same predict() body works on Anthropic: `litellm.drop_params=True`, and omit the
`temperature` param (deprecated for opus-4-8 → 400 without this).

**Numbers (val, 16 papers):**
- meaning_capture: **0.536** (baseline 0.414, iter1 0.418) — **+0.12, far outside ±0.03 noise**
- variant_coverage: 0.888 (= iter1)
- sentence_coverage: 0.513 (iter1 0.421) — +0.09

**Effect:** **better (CHAMPION).** Largest single move of the run, and above
anything in jun4 (best 0.467).

**Lesson:** the dominant lever for macro meaning_capture is **model quality**, not
prompt/coverage tactics. With coverage already maxed by iter1's prompt (0.888), a
stronger model converts found variants into faithful per-variant sentences
(direction/polarity/comparison/phenotype) — exactly the "capture quality" axis the
batch judge rewards and that gpt-5.4 plateaued on. Build all further iterations on
opus-4-8. Next: confirm with a re-run (noise check), then push prompt/decomposition
on top of opus, and sanity-check a cheaper model (sonnet-4-6) for the cost/quality
tradeoff.
