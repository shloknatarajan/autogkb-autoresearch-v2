# AutoResearch Pipeline for AutoGKB

An autonomous-research pipeline that reads the **markdown of a pharmacogenomics paper** and produces its **PharmGKB-style sentence-bench output**: the list of **variants** discussed and the list of **standardized association sentences**.

Modeled on [karpathy/autoresearch](https://github.com/karpathy/autoresearch): an agent repeatedly hacks one file (`annotation_pipeline.py`), runs a fixed evaluation, and keeps changes that improve the score. The full operating manual for that loop is in [`program.md`](program.md).

## Task

Given one paper's `markdown_content`, predict:

- **`variants`** — variant identifiers studied (rsIDs like `rs9923231`, star/HLA alleles like `CYP2C19*2`, `HLA-B*15:01`).
- **`sentences`** — standardized PharmGKB association sentences, e.g.
  > *CYP2C19 \*1/\*2 + \*2/\*2 is not associated with increased likelihood of Major Adverse Cardiac Events when treated with clopidogrel as compared to CYP2C19 \*1/\*1.*

## Layout

```
annotation_pipeline.py   # EDIT THIS — predict(markdown_content) -> {"variant_sentences": {variant: [...]}}
generate.py              # fixed driver: runs the pipeline over a split -> results/<name>/
eval.py                  # fixed harness: scoring + dev/val split + LLM judge
build_variant_bench.py   # one-time builder: collapsed bench + PharmGKB tables -> by-variant bench
program.md               # the autonomous experiment-loop manual
benchmarks/              # sentence_bench_by_variant.jsonl (target), annotation_bench.jsonl (reference)
base_data/               # articles/ (source markdown), annotations/ (full PharmGKB), variantAnnotations/ (raw tables + README.pdf)
results/                 # generations, one <pmcid>.json per paper per run (gitignored)
logs/                    # timestamped run logs (gitignored)
results.tsv              # experiment log (gitignored)
```

## How it works

Generation and scoring are **decoupled** so the same generations can be re-scored without paying to regenerate:

1. **Generate** — `generate.py` calls `annotation_pipeline.predict()` on each paper in a split and writes `results/<name>/<pmcid>.json`.
2. **Evaluate** — `eval.py` reads a results folder, looks up each paper's gold by `pmcid`, and scores.

All model calls go through **litellm**, so any provider works — swap models by changing the model string in `annotation_pipeline.py`.

### Scoring

The benchmark groups gold sentences **by variant** (`{variant -> [sentences]}`). Both metrics are **recall**: did the pipeline produce what's in the gold set? Extra predicted items are **not** penalized.

- **Variant coverage** — fraction of gold variants found, after normalization.
- **Meaning capture per variant (LLM judge)** — for each gold variant, one batch judge call sees its gold sentences and the pipeline's predicted sentences *for that variant* and returns a single `meaning_capture` score in 0–1: what fraction of the gold meaning is recovered (paraphrase / merge / split allowed; strict on direction and polarity — *increased* ≠ *decreased*, *is* ≠ *is not associated*; partial credit for the right association missing a qualifier). **`meaning_capture`** macro-averages that score across variants (each equal) then across papers, and **is the primary metric**. (A micro `sentence_coverage` is printed for information only.)

The judge model is fixed in `eval.py` (default `gpt-5.4-mini`) and is independent of the pipeline model. With only 32 papers, `eval.py` holds a deterministic **dev/val split**; develop against dev, report on the held-out val.

## Usage

```bash
uv sync                                              # install deps (incl. litellm)
# put provider keys in .env (e.g. OPENAI_API_KEY=...); eval.py/generate.py load it

uv run generate.py --out results/baseline --split val   # generate predictions
uv run eval.py results/baseline                          # score them

uv run generate.py --out results/smoke --split dev --limit 2   # quick smoke test
```

The autonomous loop (timestamped run id for folder + log) — see `program.md`:

```bash
TS=$(date +%Y%m%d-%H%M%S)
{ uv run generate.py --out results/$TS --split val && uv run eval.py results/$TS; } > logs/$TS.log 2>&1
grep "^meaning_capture:" logs/$TS.log
```

## Baseline

The baseline must be re-measured under the per-variant `meaning_capture` metric
(the figures below are pre-refactor `sentence_coverage` numbers, kept only for
reference): single-shot `gpt-4o-mini` pipeline, `gpt-5.4-mini` judge, held-out val
set (16 papers).

| metric | value |
|---|---|
| meaning_capture | _to be measured_ |
| variant_coverage | 0.539 |
| sentence_coverage (pre-refactor) | 0.385 |

See `results.tsv` for the running experiment log.
