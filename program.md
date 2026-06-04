# autogkb-autoresearch

This is an experiment to have the LLM autonomously build and improve a pipeline that reads the **markdown content of a pharmacogenomics paper** and produces the **PharmGKB-style sentence-bench output** for that paper: the list of **variants** discussed and the list of **standardized association sentences**.

It is modeled on [karpathy/autoresearch](https://github.com/karpathy/autoresearch): you are an autonomous researcher who repeatedly hacks one file, runs a fixed evaluation, and keeps changes that improve the score.

## The task

Given a single paper's `markdown_content`, predict a mapping from each variant to
the standardized PharmGKB association sentences asserting an association about it:

- `variant_sentences`: `{ variant -> [sentences] }`, where the keys are variant
  identifiers discussed in the paper (rsIDs like `rs9923231`, or star/HLA alleles
  like `CYP2C19*2`, `HLA-B*15:01`) and each value is the list of standardized
  PharmGKB association sentences about that variant (e.g. *"CYP2C19 \*1/\*2 + \*2/\*2
  is not associated with increased likelihood of Major Adverse Cardiac Events when
  treated with clopidogrel as compared to CYP2C19 \*1/\*1."*). A variant with no
  reported association still appears as a key mapped to `[]`.

The ground truth lives in `benchmarks/sentence_bench_by_variant.jsonl` (32 records),
built from the flat `sentence_bench_collapsed.jsonl` by `build_variant_bench.py`
(which recovers each sentence's variant from the `Variant/Haplotypes` column of the
raw PharmGKB tables in `base_data/variantAnnotations/`). Each record:

```
{ "pmcid", "pmid", "variant_sentences": { variant: [...] }, "markdown_content": "..." }
```

The richer `benchmarks/annotation_bench.jsonl` and the raw PharmGKB tables in `base_data/variantAnnotations/` (field definitions are in `base_data/variantAnnotations/README.pdf`) are **reference material** — useful for understanding what a "good" sentence looks like — but are **not** the scored target. Only `variants` and `sentences` are scored. (`base_data/articles/` and `base_data/annotations/` hold the source markdown and full PharmGKB annotations per paper.)

## Setup

To set up a new experiment, work with the user to:

1. **Agree on a run tag**: propose a tag based on today's date (e.g. `jun1`). The branch `autoresearch/<tag>` must not already exist — this is a fresh run.
2. **Create the branch**: `git checkout -b autoresearch/<tag>` from current `main`.
3. **Read the in-scope files**: The repo is small. Read these for full context:
   - `README.md` / this `program.md` — repository context.
   - `eval.py` — fixed harness: data loading, the dev/val split, scoring, and the LLM judge. **Do not modify.**
   - `generate.py` — fixed driver: runs the pipeline over a split and writes generations to `results/`. **Do not modify.**
   - `annotation_pipeline.py` — the file you modify. It exposes `predict()` and contains all extraction logic.
4. **Verify data exists**: Check that `benchmarks/sentence_bench_by_variant.jsonl` is present (rebuild it with `uv run build_variant_bench.py` if needed) and that `uv run eval.py --help` runs. Confirm credentials for your provider are set (e.g. `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, loaded from `.env`) for both the pipeline model and the judge.
5. **Initialize results.tsv**: Create `results.tsv` with just the header row. The baseline is recorded after the first run.
6. **Confirm and go.**

Once you get confirmation, kick off the experimentation.

## The pipeline contract

`annotation_pipeline.py` is the **only file you edit**. It must expose:

```python
def predict(markdown_content: str) -> dict:
    """Return {"variant_sentences": {variant: list[str]}} for one paper."""
```

All model calls go through **litellm** (`litellm.completion(model=..., messages=...)`) so any provider works — swap models by changing the `model=` string. Everything is fair game inside this file: prompting strategy, few-shot examples, multi-step decomposition (find variants → draft sentences → refine), regex/dictionary injection of known variants, prompt optimizers (GEPA, DSPy), retrieval from `base_data/variantAnnotations/`, ensembling, etc. (See `IDEAS.md`.)

**What you CAN do:**
- Modify `annotation_pipeline.py` freely. Change models, prompts, decomposition, post-processing.
- Add dependencies via `uv add <pkg>` when an idea needs them.
- Explore online sources/add new packages that might be helpful. Some ideas are listed in IDEAS.md

**What you CANNOT do:**
- Modify `eval.py` or `generate.py`. They are the ground-truth harness: the dev/val split, the scoring, and the **LLM judge** all live there. You may not tune the judge or peek at val gold to game the score.
- Change the scored target. Only the predicted `variant_sentences` mapping counts.

## Generation and evaluation

Generation and scoring are **decoupled**:

1. **Generate** — `uv run generate.py --out results/<name> --split val` runs `predict()` over the val papers and writes one file per paper: `results/<name>/<pmcid>.json == {"pmcid", "variant_sentences": {variant: [...]}}`.
2. **Evaluate** — `uv run eval.py results/<name>` reads that folder, looks up each paper's gold by `pmcid`, scores, and prints a summary.

This means you can re-score the same generations without paying to regenerate, and inspect any paper's raw output under `results/`. Scoring has two parts.

### Variant coverage (recall only)

We only care about **coverage**: did the pipeline find the variants that matter? Extra/spurious predicted variants are **not** penalized. Identifiers are normalized before comparison (uppercase gene, canonical `rsID` and `GENE*allele` / `HLA-X*NN:NN` forms). The variants are the **keys** of the predicted/gold `variant_sentences` mappings.

```
variant_coverage = (# gold variants matched by some predicted variant) / (# gold variants)
```
Reported micro-averaged across the val set.

### Meaning capture per variant (batch LLM judge)

The benchmark groups gold sentences **by variant**. For each gold variant we make **one** judge call: that variant's gold sentences and the pipeline's predicted sentences *for that same variant* (looked up by normalized id) are handed to the judge together, and it returns a single `capture` score in 0–1 — the fraction of that variant's gold meaning recovered by the predictions (paraphrase, merge, and split are allowed; partial credit applies). Extra/spurious predicted sentences are **not** penalized (same philosophy as variant coverage):

```
capture(variant)   = fraction of that variant's gold meaning captured by its predictions (0..1)
meaning_capture    = mean over a paper's variants of capture(variant),  then mean over papers
```
Aggregation is **macro** — each variant counts equally regardless of how many sentences it has, and each paper counts equally. Variants with no gold sentences are skipped for capture (they still count toward `variant_coverage`). **`meaning_capture` is the primary metric (higher is better).** A micro-averaged `sentence_coverage` (gold-equivalent meaning captured / total gold sentences) is printed for information only and does not drive keep/discard.

The judge prompt (lives in `eval.py`, not editable):

```
You are grading a pharmacogenomics information-extraction system. You are given
two lists of "standardized association sentences" about the SAME paper and the
SAME genetic variant:

GOLD sentences (the reference) and PREDICTED sentences (the system output).

Each sentence asserts an association between a genetic variant/genotype and an
outcome. Evaluate what fraction of the meaning in the GOLD sentences is captured
by the PREDICTED sentences.

Score only recall of the gold meanings. Extra predicted associations are not
penalized unless they make it unclear whether a gold meaning is actually captured.

Be critical, but allow multiple phrasings of the same association. A prediction can
capture a gold meaning even when it combines, splits, reorders, or paraphrases the
gold sentence. It must still agree on the substantive association:
  - the variant(s)/genotype(s), including alleles or diplotypes when relevant
  - the drug(s) or substance involved, if any
  - the phenotype / outcome (e.g. dose, MACE, toxicity, metabolizer status)
  - the direction of effect (increased vs decreased / higher vs lower)
  - the polarity ("is associated" vs "is NOT associated")
  - the comparison group / comparison allele, when stated

Do NOT give credit when direction or polarity is reversed, when a different
phenotype or drug is substituted, or when a genotype-specific finding is
generalized in a way that loses the gold meaning. Give partial credit for partially
captured gold meaning, such as the right association but missing an important
qualifier, population, comparison, or genotype detail.

Return JSON only:
{ "meaning_capture": <number from 0.0 to 1.0> }
```

The judge model is fixed in `eval.py` and is independent of whatever model your pipeline uses.

### Dev / val split (anti-overfitting)

With only 32 papers, a pipeline can memorize them. `eval.py` therefore splits the bench into a **dev set** (you may inspect these papers and their gold labels while iterating) and a **held-out val set** (scored; do not inspect or hard-code its gold). The primary metric is computed on **val**. The split is deterministic and defined in `eval.py`.

## Output format

When `eval.py` finishes it prints a summary block:

```
---
meaning_capture:    0.612
variant_coverage:   0.810
sentence_coverage:  0.587
num_papers:         16
num_gold_sentences: 78
generations:        results/<name>
judge_model:        ...
total_seconds:      ...
```

Extract the key metric from the log:

```
grep "^meaning_capture:" logs/<run-id>.log
```

## Logging results

When an experiment is done, log it to `results.tsv` (tab-separated, NOT comma — commas break in descriptions). Leave `results.tsv` **untracked** by git.

Header and 5 columns:

```
commit	meaning_capture	variant_coverage	status	description
```

1. git commit hash (short, 7 chars)
2. `meaning_capture` on val (e.g. `0.612`) — use `0.000` for crashes
3. `variant_coverage` on val (e.g. `0.810`) — use `0.0` for crashes
4. status: `keep`, `discard`, or `crash`
5. short text description of what this experiment tried

Example:

```
commit	meaning_capture	variant_coverage	status	description
a1b2c3d	0.385	0.539	keep	baseline: single-shot gpt-4o-mini pipeline, gpt-5.4-mini judge
b2c3d4e	0.480	0.710	keep	two-stage: extract variants, then draft sentences per variant
c3d4e5f	0.470	0.720	discard	add few-shot examples from dev set (no gain)
d4e5f6g	0.000	0.0	crash	switch judge-side schema (broke predict output)
```

## The experiment loop

The experiment runs on a dedicated branch (e.g. `autoresearch/jun1` or `autoresearch/jun1-a`).

**Exit policy**: run a maximum of **15 experiment iterations** (each iteration = one pass through the steps below: edit → generate → eval → log → keep/discard). The baseline run does not count toward the 15. The iteration counter is the number of non-baseline rows in `results.tsv`. After the 15th iteration completes, **stop** and write a short final summary (best `meaning_capture`, what worked, what didn't). Until then, do not pause to ask the human whether to continue.

LOOP until 15 iterations are done:

1. Look at the git state: the current branch/commit we're on, and count non-baseline rows in `results.tsv` — if that count is ≥ 15, stop.
2. Tune `annotation_pipeline.py` with an experimental idea by directly hacking the code.
3. `git commit`.
4. Run the experiment. Use one timestamped run id for both the generations folder and the log, and **redirect everything to a timestamped log file under `logs/`** (do NOT use tee or let output flood your context):
   ```
   TS=$(date +%Y%m%d-%H%M%S)
   { uv run generate.py --out results/$TS --split val && uv run eval.py results/$TS; } > logs/$TS.log 2>&1
   ```
5. Read out the results: `grep "^meaning_capture:\|^variant_coverage:" logs/$TS.log`.
6. If the grep output is empty, the run crashed. Run `tail -n 50 logs/$TS.log` to read the stack trace and attempt a fix. If you can't get it working after a few attempts, give up on that idea.
7. Record the results in `results.tsv` (do NOT commit `results.tsv`; leave it untracked).
8. If `meaning_capture` improved (higher), you "advance" the branch, keeping the git commit.
9. If `meaning_capture` is equal or worse, `git reset` back to where you started.

You are a completely autonomous researcher trying things out. If they work, keep. If they don't, discard. You advance the branch so you can iterate. If you feel stuck, you can rewind, but do this very sparingly (if ever).

**Logs**: every run writes a timestamped log to `logs/<run-id>.log` and its generations to `results/<run-id>/`. Both `logs/` and `results/` are untracked by git.

**The first run**: Your very first run should always establish the baseline — a minimal, honest `predict()` (e.g. one straightforward litellm call) — run as-is and recorded as `keep` / `baseline`.

**Simplicity criterion**: All else equal, simpler is better. A small F1 gain that adds ugly complexity may not be worth it; a change that removes code while holding or improving F1 is a clear win. Weigh complexity cost against the improvement magnitude.

**Cost/time awareness**: Each run makes model calls for all val papers plus one judge call per paper. Keep runs reasonable; if a run hangs or exceeds ~15 minutes, kill it and treat it as a failure (discard and revert). Avoid changes that explode token cost for negligible F1.

**Crashes**: If a run crashes (bad output schema, API error, a bug), use judgment: if it's something dumb and easy (a typo, a missing key in the returned dict, a transient API error), fix it and re-run. If the idea itself is fundamentally broken, skip it, log `crash`, and move on.

**Do not overfit the val set**: never inspect or hard-code val gold labels. Develop ideas against the dev set and let val be the honest measure.

**Don't stall before the cap**: until the 15-iteration cap is reached, do NOT pause to ask the human "should I keep going?" — keep generating and testing ideas autonomously. If you run out of obvious ideas, think harder: re-read the gold sentences on the dev set for patterns you're missing, study field definitions in `base_data/variantAnnotations/README.pdf`, mine known variants from the `base_data/variantAnnotations/` tables, try multi-stage decomposition, try a stronger model, combine previous near-misses, or attempt more radical prompting changes. The human can always interrupt early; otherwise the loop ends at 15 iterations.
```