# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An **autoresearch loop** (modeled on [karpathy/autoresearch](https://github.com/karpathy/autoresearch)): an autonomous agent repeatedly hacks **one file** (`annotation_pipeline.py`), runs a **fixed** evaluation, and records the result. Every experiment is committed and **kept** — successes and regressions alike. The branch history, `results.tsv`, and `attempts/<tag>/` are a durable record of what worked and what didn't. `program.md` is the full operating manual for the loop; **read it before running the loop.**

The task: given one pharmacogenomics paper's `markdown_content`, predict `{"variant_sentences": {variant -> [standardized PharmGKB association sentences]}}` — the variants studied (rsIDs like `rs9923231`, star/HLA alleles like `CYP2C19*2`, `HLA-B*15:01`) and the association sentences asserted about each.

## Commands

```bash
uv sync                                                  # install deps
# provider keys go in .env (OPENAI_API_KEY / ANTHROPIC_API_KEY); generate.py & eval.py load it via dotenv

uv run generate.py --out results/<name> --split val      # run predict() over a split -> results/<name>/<pmcid>.json
uv run generate.py --out results/smoke --split dev --limit 2   # quick smoke test
uv run eval.py results/<name>                            # score a generations folder
uv run eval.py results/<name> --judge-model gpt-4o       # override judge model

uv run build_variant_bench.py                            # rebuild the by-variant benchmark (one-time; needs the collapsed bench + raw TSVs)
uv run ruff check . / uv run ruff format .               # lint / format
```

The canonical loop run (timestamped id shared by the generations folder and the log; **redirect to a log file — never tee or let output flood context**):

```bash
TS=$(date +%Y%m%d-%H%M%S)
{ uv run generate.py --out results/$TS --split val && uv run eval.py results/$TS; } > logs/$TS.log 2>&1
grep "^meaning_capture:\|^variant_coverage:" logs/$TS.log   # empty grep == crash; tail -n 50 logs/$TS.log for the trace
```

## Editable vs. fixed (the hard rule)

- **`annotation_pipeline.py`** — the ONLY pipeline file you edit. Must expose `predict(markdown_content) -> {"variant_sentences": {variant: list[str]}}`. Prompting, decomposition, models, regex injection, retrieval, ensembling — all fair game here.
- **`tools/`** — a shared, importable library reused across attempts/runs (`from tools.<module> import ...`). You MAY add or extend tools here when logic is reusable beyond one attempt. Existing: `regex_variants.py` (rsID/star/HLA extraction), `term_lookup.py` (ClinPGx/PharmGKB normalization), `cross_file.py` (replicate a sentence under every variant it names).
- **`eval.py` and `generate.py` — DO NOT MODIFY.** They are the ground-truth harness: the dev/val split, the scoring, and the LLM judge all live in `eval.py`. Tuning the judge or peeking at val gold games the metric.

## Architecture

**Generation and scoring are decoupled** so the same generations can be re-scored without paying to regenerate:

1. `generate.py` calls `annotation_pipeline.predict()` per paper in a split → writes `results/<name>/<pmcid>.json`.
2. `eval.py` reads that folder, looks up each paper's gold by `pmcid`, and scores. It is forgiving about output shape (`coerce_prediction`) and never crashes on a bad pipeline result.

All model calls go through **litellm**, so any provider works by changing the model string. Notes baked into `annotation_pipeline.py`: `litellm.drop_params = True` (so OpenAI's `response_format=json_object` is silently dropped for providers that reject it; JSON is parsed from text via `_extract_json_object`), and `temperature` is omitted for `opus-4-8` (which deprecates it).

### Scoring (both metrics are RECALL — extra predicted items are never penalized)

`eval.py` was revised (2026-06-23) to measure *extraction skill* rather than PharmGKB filing convention — see `ANNOTATION_AMBIGUITY.md`. It now prints **primary** and **secondary** blocks; primary metric values are **NOT comparable to pre-revision runs (jun1..jun5)**.

**Primary:**
- **`meaning_capture` — the PRIMARY metric, now PAPER-LEVEL and representation-invariant.** All of a paper's *distinct* gold sentences are pooled and scored against the pipeline's *full* predicted-sentence pool (variant keys ignored), macro-averaged across papers. A correct association filed under a different-but-valid key (granularity, cross-filing, `*1` reference, rsID-vs-star) is no longer a miss. The judge stays **strict on direction/polarity/phenotype** (*increased* ≠ *decreased*, *is* ≠ *is not associated*).
- **`variant_coverage`** — recall over gold variant keys, accepting **rsID ↔ star-allele equivalence** via a curated single-defining-SNP table (`STAR_ALLELE_DEFINING_RSID`) and bipartite matching (one prediction satisfies at most one gold key).

**Secondary (PharmGKB-convention adherence, kept for comparison):** `meaning_capture_perkey` (old per-variant macro), `variant_coverage_strict` (old exact-key match), `sentence_coverage` (micro). Pass `--no-perkey` to skip the per-variant judge calls and halve judge cost.

The judge model is fixed in `eval.py` (default `gpt-5.4-mini`) and is independent of the pipeline model. The judge is **noisy** (~±0.05 swing on identical input, worse on papers with only 1–2 distinct gold sentences since each is near-binary) — validate champions with repeat runs. A champion that already follows PharmGKB conventions scores ~the same on primary and secondary (e.g. 0.590 vs 0.599); the primary metric's benefit shows up for agents that get the biology right but structure it differently.

### Dev/val split (anti-overfitting)

Only 32 papers. `split_bench` in `eval.py` deterministically alternates over pmcid-sorted records into **dev** (inspect freely while iterating) and **held-out val** (the honest metric — never inspect or hard-code its gold). Develop against dev, report on val.

## The loop's memory (how learning compounds)

- **`results.tsv`** — experiment log, **tab-separated** (commas break descriptions) and **kept untracked by git**. Columns: `commit · meaning_capture · variant_coverage · effect · description`. `effect` (`better`/`worse`/`similar`/`crash`/`baseline`) is an informational label vs. best-so-far — it does **not** trigger a revert.
- **`attempts/<tag>/`** — the durable lab notebook (one tag per run, e.g. `jun5`). After every experiment, save `<label>_<hash>/` with the exact `annotation_pipeline.py`, `results.txt` (the eval `---` block), and `notes.md` (hypothesis · change · numbers · **lesson/why**); append a one-liner to `LEARNINGS.md`. Read `LEARNINGS.md` before picking the next idea. `SUMMARY.md` is written once at the end. **Past runs (jun1/jun2/jun4) used a different `eval.py`, so their absolute numbers are NOT comparable to current runs.**

Nothing is reverted: build the next idea on the best-so-far file (`git checkout <best-commit> -- annotation_pipeline.py`) and commit forward; rejected attempts stay as ancestor commits and in `attempts/`.

## Benchmarks & data

- `benchmarks/sentence_bench_by_variant.jsonl` — **the scored target** (32 records, `{pmcid, pmid, variant_sentences, markdown_content}`), built from `sentence_bench_collapsed.jsonl` by `build_variant_bench.py`.
- `benchmarks/annotation_bench.jsonl` + `base_data/variantAnnotations/` (raw PharmGKB TSVs; field defs in `README.pdf`) — **reference material only**, useful for understanding good sentences; not scored.
- `base_data/articles/` (source markdown) and `base_data/annotations/` (full PharmGKB annotations per paper).
- `logs/` and `results/` are untracked.
