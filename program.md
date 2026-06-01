# autogkb-autoresearch

This is an experiment to have the LLM autonomously build and improve a pipeline that reads the **markdown content of a pharmacogenomics paper** and produces the **PharmGKB-style sentence-bench output** for that paper: the list of **variants** discussed and the list of **standardized association sentences**.

It is modeled on [karpathy/autoresearch](https://github.com/karpathy/autoresearch): you are an autonomous researcher who repeatedly hacks one file, runs a fixed evaluation, and keeps changes that improve the score.

## The task

Given a single paper's `markdown_content`, predict:

- `variants`: a list of variant identifiers discussed in the paper (rsIDs like `rs9923231`, or star/HLA alleles like `CYP2C19*2`, `HLA-B*15:01`).
- `sentences`: a list of standardized PharmGKB association sentences (e.g. *"CYP2C19 \*1/\*2 + \*2/\*2 is not associated with increased likelihood of Major Adverse Cardiac Events when treated with clopidogrel as compared to CYP2C19 \*1/\*1."*).

The ground truth lives in `benchmarks/sentence_bench_collapsed.jsonl` (32 records). Each record:

```
{ "pmcid", "pmid", "variants": [...], "sentences": [...], "markdown_content": "..." }
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
4. **Verify data exists**: Check that `benchmarks/sentence_bench_collapsed.jsonl` is present and that `uv run eval.py --help` runs. Confirm credentials for your provider are set (e.g. `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, loaded from `.env`) for both the pipeline model and the judge.
5. **Initialize results.tsv**: Create `results.tsv` with just the header row. The baseline is recorded after the first run.
6. **Confirm and go.**

Once you get confirmation, kick off the experimentation.

## The pipeline contract

`annotation_pipeline.py` is the **only file you edit**. It must expose:

```python
def predict(markdown_content: str) -> dict:
    """Return {"variants": list[str], "sentences": list[str]} for one paper."""
```

All model calls go through **litellm** (`litellm.completion(model=..., messages=...)`) so any provider works — swap models by changing the `model=` string. Everything is fair game inside this file: prompting strategy, few-shot examples, multi-step decomposition (find variants → draft sentences → refine), regex/dictionary injection of known variants, prompt optimizers (GEPA, DSPy), retrieval from `base_data/variantAnnotations/`, ensembling, etc. (See `IDEAS.md`.)

**What you CAN do:**
- Modify `annotation_pipeline.py` freely. Change models, prompts, decomposition, post-processing.
- Add dependencies via `uv add <pkg>` when an idea needs them.

**What you CANNOT do:**
- Modify `eval.py` or `generate.py`. They are the ground-truth harness: the dev/val split, the scoring, and the **LLM judge** all live there. You may not tune the judge or peek at val gold to game the score.
- Change the scored target. Only `variants` and `sentences` count.

## Generation and evaluation

Generation and scoring are **decoupled**:

1. **Generate** — `uv run generate.py --out results/<name> --split val` runs `predict()` over the val papers and writes one file per paper: `results/<name>/<pmcid>.json == {"pmcid", "variants", "sentences"}`.
2. **Evaluate** — `uv run eval.py results/<name>` reads that folder, looks up each paper's gold by `pmcid`, scores, and prints a summary.

This means you can re-score the same generations without paying to regenerate, and inspect any paper's raw output under `results/`. Scoring has two parts.

### Variant coverage (recall only)

We only care about **coverage**: did the pipeline find the variants that matter? Extra/spurious predicted variants are **not** penalized. Identifiers are normalized before comparison (uppercase gene, canonical `rsID` and `GENE*allele` / `HLA-X*NN:NN` forms).

```
variant_coverage = (# gold variants matched by some predicted variant) / (# gold variants)
```
Reported micro-averaged across the val set.

### Sentence coverage (batch LLM judge)

For each paper we make **one** judge call: the full group of gold sentences and the full group of predicted sentences are handed to the judge together, and it returns a **one-to-one matching** of predicted ↔ gold sentences that assert the *same* association. We score **coverage (recall)** — did the pipeline produce each gold association? Extra/spurious predicted sentences are **not** penalized (same philosophy as variant coverage):

```
sentence_coverage = (# gold sentences matched) / (# gold sentences)
```
Reported micro-averaged across the val set. **`sentence_coverage` is the primary metric (higher is better).** `sentence_precision` (matched / predicted) is printed for information only and does not drive keep/discard.

The judge prompt (lives in `eval.py`, not editable):

```
You are grading a pharmacogenomics information-extraction system. You are given
two lists of "standardized association sentences" about the SAME paper:

GOLD sentences (the reference) and PREDICTED sentences (the system output).

Each sentence asserts a single association between a genetic variant/genotype and
an outcome. Match a PREDICTED sentence to a GOLD sentence ONLY IF they assert the
same association. They must agree on ALL of:
  - the variant(s)/genotype(s) (e.g. rs9923231, CYP2C19*2, *1/*2)
  - the drug(s) or substance involved (if any)
  - the phenotype / outcome (e.g. dose, MACE, toxicity, metabolizer status)
  - the DIRECTION of effect (increased vs decreased / higher vs lower)
  - the POLARITY ("is associated" vs "is NOT associated")
  - the comparison group / comparison allele, when stated

Differences in wording, order, or formatting do NOT matter — only the asserted
meaning. Be strict about direction and polarity: "is associated with increased"
and "is not associated" (or "decreased") describe DIFFERENT associations and MUST
NOT be matched.

Each gold sentence matches at most one predicted sentence and vice versa (one-to-one).

Return JSON only:
{ "matches": [ { "gold_index": <int>, "pred_index": <int> }, ... ] }
Include only true matches. Omit anything unmatched.
```

The judge model is fixed in `eval.py` and is independent of whatever model your pipeline uses.

### Dev / val split (anti-overfitting)

With only 32 papers, a pipeline can memorize them. `eval.py` therefore splits the bench into a **dev set** (you may inspect these papers and their gold labels while iterating) and a **held-out val set** (scored; do not inspect or hard-code its gold). The primary metric is computed on **val**. The split is deterministic and defined in `eval.py`.

## Output format

When `eval.py` finishes it prints a summary block:

```
---
sentence_coverage:  0.587
variant_coverage:   0.810
sentence_precision: 0.640
num_papers:         16
num_pred_sentences: 71
num_gold_sentences: 78
generations:        results/<name>
judge_model:        ...
total_seconds:      ...
```

Extract the key metric from the log:

```
grep "^sentence_coverage:" logs/<run-id>.log
```

## Logging results

When an experiment is done, log it to `results.tsv` (tab-separated, NOT comma — commas break in descriptions). Leave `results.tsv` **untracked** by git.

Header and 5 columns:

```
commit	sentence_coverage	variant_coverage	status	description
```

1. git commit hash (short, 7 chars)
2. `sentence_coverage` on val (e.g. `0.587`) — use `0.000` for crashes
3. `variant_coverage` on val (e.g. `0.810`) — use `0.0` for crashes
4. status: `keep`, `discard`, or `crash`
5. short text description of what this experiment tried

Example:

```
commit	sentence_coverage	variant_coverage	status	description
a1b2c3d	0.327	0.539	keep	baseline: single-shot prompt, gpt-4o-mini
b2c3d4e	0.480	0.710	keep	two-stage: extract variants, then draft sentences
c3d4e5f	0.470	0.720	discard	add few-shot examples from dev set (no gain)
d4e5f6g	0.000	0.0	crash	switch judge-side schema (broke predict output)
```

## The experiment loop

The experiment runs on a dedicated branch (e.g. `autoresearch/jun1` or `autoresearch/jun1-a`).

LOOP FOREVER:

1. Look at the git state: the current branch/commit we're on.
2. Tune `annotation_pipeline.py` with an experimental idea by directly hacking the code.
3. `git commit`.
4. Run the experiment. Use one timestamped run id for both the generations folder and the log, and **redirect everything to a timestamped log file under `logs/`** (do NOT use tee or let output flood your context):
   ```
   TS=$(date +%Y%m%d-%H%M%S)
   { uv run generate.py --out results/$TS --split val && uv run eval.py results/$TS; } > logs/$TS.log 2>&1
   ```
5. Read out the results: `grep "^sentence_coverage:\|^variant_coverage:" logs/$TS.log`.
6. If the grep output is empty, the run crashed. Run `tail -n 50 logs/$TS.log` to read the stack trace and attempt a fix. If you can't get it working after a few attempts, give up on that idea.
7. Record the results in `results.tsv` (do NOT commit `results.tsv`; leave it untracked).
8. If `sentence_coverage` improved (higher), you "advance" the branch, keeping the git commit.
9. If `sentence_coverage` is equal or worse, `git reset` back to where you started.

You are a completely autonomous researcher trying things out. If they work, keep. If they don't, discard. You advance the branch so you can iterate. If you feel stuck, you can rewind, but do this very sparingly (if ever).

**Logs**: every run writes a timestamped log to `logs/<run-id>.log` and its generations to `results/<run-id>/`. Both `logs/` and `results/` are untracked by git.

**The first run**: Your very first run should always establish the baseline — a minimal, honest `predict()` (e.g. one straightforward litellm call) — run as-is and recorded as `keep` / `baseline`.

**Simplicity criterion**: All else equal, simpler is better. A small F1 gain that adds ugly complexity may not be worth it; a change that removes code while holding or improving F1 is a clear win. Weigh complexity cost against the improvement magnitude.

**Cost/time awareness**: Each run makes model calls for all val papers plus one judge call per paper. Keep runs reasonable; if a run hangs or exceeds ~15 minutes, kill it and treat it as a failure (discard and revert). Avoid changes that explode token cost for negligible F1.

**Crashes**: If a run crashes (bad output schema, API error, a bug), use judgment: if it's something dumb and easy (a typo, a missing key in the returned dict, a transient API error), fix it and re-run. If the idea itself is fundamentally broken, skip it, log `crash`, and move on.

**Do not overfit the val set**: never inspect or hard-code val gold labels. Develop ideas against the dev set and let val be the honest measure.

**NEVER STOP**: Once the loop has begun (after initial setup), do NOT pause to ask the human whether to continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". The human may be asleep or away and expects you to work *indefinitely* until manually stopped. You are autonomous. If you run out of ideas, think harder — re-read the gold sentences on the dev set for patterns you're missing, study the field definitions in `variantAnnotations/README.pdf`, mine known variants from `variantAnnotations/` tables, try multi-stage decomposition, try a stronger model, combine previous near-misses, or attempt more radical prompting changes. Study field definitions in `base_data/variantAnnotations/README.pdf` and mine known variants from the `base_data/variantAnnotations/` tables. The loop runs until the human interrupts you, period.
```