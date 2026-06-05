# autogkb-autoresearch

This is an experiment to have the LLM autonomously build and improve a pipeline that reads the **markdown content of a pharmacogenomics paper** and produces the **PharmGKB-style sentence-bench output** for that paper: the list of **variants** discussed and the list of **standardized association sentences**.

It is modeled on [karpathy/autoresearch](https://github.com/karpathy/autoresearch): you are an autonomous researcher who repeatedly hacks one file, runs a fixed evaluation, and records the result. Every experiment is committed and kept — successes and regressions alike — so the branch is a durable record of what worked and what didn't.

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
6. **Initialize the attempts memory**: Create `attempts/<tag>/` with an empty `LEARNINGS.md` (header only). This folder is your lab notebook — you write to it after every experiment and read it before every new idea (see "Learning from past attempts"). Skim the earlier runs' `attempts/*/LEARNINGS.md` now for prior lessons.
7. **Confirm and go.**

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
Aggregation is **macro** — each variant counts equally regardless of how many sentences it has, and each paper counts equally. Variants with no gold sentences are skipped for capture (they still count toward `variant_coverage`). **`meaning_capture` is the primary metric (higher is better).** A micro-averaged `sentence_coverage` (gold-equivalent meaning captured / total gold sentences) is printed for information only.

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
commit	meaning_capture	variant_coverage	effect	description
```

1. git commit hash (short, 7 chars)
2. `meaning_capture` on val (e.g. `0.612`) — use `0.000` for crashes
3. `variant_coverage` on val (e.g. `0.810`) — use `0.0` for crashes
4. effect: a **non-actionable** record of how `meaning_capture` moved vs the
   best-so-far — `better`, `worse`, `similar` (within noise), or `crash`. This is
   just a label for later reading; it does **not** trigger a revert. Every
   experiment is kept regardless (see the loop below).
5. short text description of what this experiment tried — and, since regressions
   are kept too, *why it didn't work* when you can tell.

Example:

```
commit	meaning_capture	variant_coverage	effect	description
a1b2c3d	0.385	0.539	better	baseline: single-shot gpt-4o-mini pipeline, gpt-5.4-mini judge
b2c3d4e	0.480	0.710	better	two-stage: extract variants, then draft sentences per variant
c3d4e5f	0.470	0.720	similar	add few-shot examples from dev set (no gain; within noise)
d4e5f6g	0.000	0.0	crash	switch judge-side schema (broke predict output)
```

## Learning from past attempts — the `attempts/` memory

`attempts/<tag>/` is your durable lab notebook and the loop's learning substrate.
The git history is not the point; **this folder is**. After every experiment you
SAVE the attempt here; before every new idea you READ it (and earlier runs'
`attempts/*/`) so you build on what worked and never re-run a known failure.

Layout:

```
attempts/<tag>/
  LEARNINGS.md                 # running digest: one line per attempt — effect + the lesson
  SUMMARY.md                   # written once at the end of the run
  <label>_<hash>/              # one dir per experiment, e.g. baseline_0cfeeb6, iter7_d5528b7
    annotation_pipeline.py     # the exact code for this attempt
    <helper>.py                # any module annotation_pipeline.py imports (e.g. cross_file.py)
    results.txt                # the eval summary block (the `---` block eval.py prints)
    notes.md                   # hypothesis · what changed vs best-so-far · numbers · effect · LESSON
```

`notes.md` is the important part. Write the **LESSON** in plain words: what this
experiment tried, whether it helped, and **why** (the mechanism) — so a future
iteration can act on it. `LEARNINGS.md` is the cheap-to-scan digest: append one
line per experiment (`<label> <meaning_capture>/<coverage> <effect> — <lesson>`),
so you can re-read the whole history in one glance before choosing the next idea.

## The experiment loop

The experiment runs on a dedicated branch (e.g. `autoresearch/jun1` or `autoresearch/jun1-a`).

**Keep every experiment.** There is no keep/discard and no revert. Each iteration
is committed and **kept on the branch**, whether it helped or not — the whole point
is a durable record of what worked AND what didn't. Never `git reset` away a
regression. To start the next idea you typically build on the best-performing
commit so far: restore that version of `annotation_pipeline.py` (e.g.
`git checkout <best-commit> -- annotation_pipeline.py`) and then apply the new
idea on top, committing forward. The rejected attempts remain as ancestor commits
and as rows in `results.tsv`; nothing is thrown away.

**Exit policy**: run a fixed number of **experiment iterations** `N` (each iteration = one pass through the steps below: edit → generate → eval → log). `N` is provided by the human before triggering the loop; **if no count is given, default to 10**. The baseline run does not count toward `N`. The iteration counter is the number of non-baseline rows in `results.tsv`. After the `N`th iteration completes, **stop** and write a short final summary (best `meaning_capture`, what worked, what didn't). Until then, do not pause to ask the human whether to continue.

LOOP until `N` iterations are done:

1. **Review the memory.** Read `attempts/<tag>/LEARNINGS.md` end to end (and, on the first iterations, the earlier runs' `attempts/*/LEARNINGS.md`). Open the `notes.md` of the most relevant past attempts. The point: pick the next idea informed by what already worked and what already failed — do not repeat a known failure, and prefer building on a known success. Then count non-baseline rows in `results.tsv`; if ≥ `N`, stop. Note that previous attempts use different eval.py functions so previously attempted solutions and results are not directly applicable to this autoresearch run.
2. Tune `annotation_pipeline.py` with an experimental idea by directly hacking the code (build on the best-so-far version of the file).
3. `git commit`.
4. Run the experiment. Use one timestamped run id for both the generations folder and the log, and **redirect everything to a timestamped log file under `logs/`** (do NOT use tee or let output flood your context):
   ```
   TS=$(date +%Y%m%d-%H%M%S)
   { uv run generate.py --out results/$TS --split val && uv run eval.py results/$TS; } > logs/$TS.log 2>&1
   ```
5. Read out the results: `grep "^meaning_capture:\|^variant_coverage:" logs/$TS.log`.
6. If the grep output is empty, the run crashed. Run `tail -n 50 logs/$TS.log` to read the stack trace and attempt a fix. If you can't get it working after a few attempts, give up on that idea (still save it — a crash is a lesson too).
7. Record the results in `results.tsv` (do NOT commit `results.tsv`; leave it untracked). Tag the `effect` (`better`/`worse`/`similar`/`crash`) vs the best so far — informational only.
8. **Save the attempt to memory.** Create `attempts/<tag>/<label>_<short-hash>/` and write into it: `annotation_pipeline.py` (and any helper modules it imports), `results.txt` (the `---` summary block from the log), and `notes.md` (hypothesis · what changed vs best-so-far · the numbers · effect · the **lesson** — what worked/didn't and why). Then append the one-line digest entry to `attempts/<tag>/LEARNINGS.md`. This save is what the *next* iteration learns from, so do it every time, including the baseline and crashes.
9. Keep the commit no matter the outcome. For the next idea, build on whichever attempt is best so far (restore its `annotation_pipeline.py`) — but do **not** delete or reset the others; they stay in the history and, more importantly, in `attempts/`.

You are a completely autonomous researcher trying things out. Keep every experiment — the failures are evidence too. Each experiment is saved to `attempts/<tag>/` with its lesson, and each new idea starts by reading that memory: this read→experiment→save→read loop is how you compound learning across iterations instead of rediscovering the same dead ends. Track which attempt is currently best (the final summary and the branch's end state should reflect it), build forward from it, and leave the rejected attempts in `attempts/` so the record shows what didn't work and why. When the cap is reached, write `attempts/<tag>/SUMMARY.md` (best `meaning_capture`, what worked, what didn't) drawing on `LEARNINGS.md`.

**Logs**: every run writes a timestamped log to `logs/<run-id>.log` and its generations to `results/<run-id>/`. Both `logs/` and `results/` are untracked by git.

**The first run**: Your very first run should always establish the baseline — a minimal, honest `predict()` (e.g. one straightforward litellm call) — run as-is and recorded as the `baseline` row (effect column left as `baseline`).

**Simplicity criterion**: All else equal, simpler is better. A small F1 gain that adds ugly complexity may not be worth it; a change that removes code while holding or improving F1 is a clear win. Weigh complexity cost against the improvement magnitude.

**Cost/time awareness**: Each run makes model calls for all val papers plus one judge call per paper. Keep runs reasonable; if a run hangs or exceeds ~15 minutes, kill it and log it as a `crash` (the commit still stays — nothing is reverted). Avoid changes that explode token cost for negligible F1.

**Crashes**: If a run crashes (bad output schema, API error, a bug), use judgment: if it's something dumb and easy (a typo, a missing key in the returned dict, a transient API error), fix it and re-run. If the idea itself is fundamentally broken, skip it, log `crash`, and move on.

**Do not overfit the val set**: never inspect or hard-code val gold labels. Develop ideas against the dev set and let val be the honest measure.

**Don't stall before the cap**: until the iteration cap is reached, do NOT pause to ask the human "should I keep going?" — keep generating and testing ideas autonomously. If you run out of obvious ideas, think harder: re-read the gold sentences on the dev set for patterns you're missing, study field definitions in `base_data/variantAnnotations/README.pdf`, mine known variants from the `base_data/variantAnnotations/` tables, try multi-stage decomposition, try a stronger model, combine previous near-misses, or attempt more radical prompting changes. The human can always interrupt early; otherwise the loop ends at `N` iterations.
```