# agentic — Claude Agent SDK loop with term-normalization tools

## Hypothesis

The champion is one single-shot call: the model reads the paper once, cannot re-read a
table it skimmed, and cannot check whether a variant or drug it extracted is a real
controlled-vocabulary term. Give it an agentic loop with tools — a paper search, a
deterministic regex variant extractor, a ClinPGx term lookup, and a schema-enforced
submit channel — and both `variant_coverage` (find variants buried in tables) and
`meaning_capture` (stop hallucinating terms) should rise.

## What changed vs best-so-far

Replaced `predict()`'s `litellm.completion()` with a `claude-agent-sdk` `query()` loop
(`claude-opus-4-8`, `max_turns=10`, thinking disabled to match the champion and isolate
tool use as the only variable). Four in-process MCP tools in `agent_tools.py`:

| tool | backing |
|---|---|
| `search_paper(pattern)` | regex over the paper string |
| `extract_variants()` | `tools/regex_variants.py:extract_all_variants` |
| `lookup_term(term, kind)` | `tools/term_lookup.py` (returns `name`, never the accession `id`) |
| `submit_annotations(...)` | schema-enforced output channel |

System prompt = the jun5 champion's rich PharmGKB prompt (unchanged) + a WORKFLOW
section fencing the revise step to **delete/correct only, never add volume**, because
jun5 showed 4× that adding output regresses the per-variant macro.

Built-ins disabled (`tools=[]`) and `setting_sources=[]`, so the agent has no filesystem
access and structurally cannot read `benchmarks/` (the held-out val gold) or ingest
`program.md`.

Deviation from program.md's litellm-only convention: this pipeline calls Anthropic via
claude-agent-sdk. `eval.py`'s judge is untouched and still runs on litellm.

## Numbers (val, 16 papers)

| config | meaning_capture | variant_coverage |
|---|---|---|
| champion, re-run on this harness | 0.502 | 0.888 |
| champion, jun5 recorded (3 runs) | 0.558 mean (0.536/0.598/0.540) | ~0.90 |
| **agentic, 3 runs** | **0.541 mean** (0.541/0.531/0.551), sd **0.008** | **0.888 / 0.888 / 0.888** |

Cost: **$6.13–$6.53 per val run** (~$0.40/paper), ~12 min wall clock.
Champion: ~$0.02, 56 s. So ~300× the cost and ~13× the latency.

Zero papers hit the `submit_annotations` fallback path; zero agent failures.

## Effect

**similar** — mean 0.541 sits −0.017 below the champion's jun5 mean (0.558) and +0.039
above the champion's re-run on this harness (0.502). Both deltas are inside the ±0.06
noise band. The agentic loop does not beat the champion.

## LESSON

**1. The recall tools were built to fix a problem regex structurally cannot fix.**
`variant_coverage` came back as **0.888 on all three runs, and 0.888 for the champion** —
identical to three decimals. A zero-API-cost check explains why: run
`extract_all_variants()` directly over the dev papers and intersect with dev gold, and its
**ceiling is 80/90 = 0.889**. The extractor tops out exactly where the model already was,
so it could never contribute coverage. What it misses is not noise, it's two structural
classes:

- **Wild-type reference alleles** (`UGT1A1*1`, `CYP2D6*1`). The gold requires `<GENE>*1`
  as a key, but the paper never writes that token. No regex can extract a string that
  isn't there — only the prompt can invent it, which the champion prompt already does.
- **Under-enumerated HLA alleles** (`HLA-B*35:10`, `HLA-DRB1*08:01`, … — 6 missed on
  PMC5561238 alone), plus rsIDs split across table cells (`rs9923231` on PMC4706412).

This reproduces jun5's ensemble result (zero coverage gain) by a different route, and
confirms its diagnosis: the residual ~11% is genuinely hard, not a retrieval failure.
**Before building a tool to raise recall, compute the tool's offline ceiling against gold.
It costs nothing and would have pre-empted this entire experiment.**

**2. The metric cannot see what the agent actually improved.** The agent produced 201
variant keys vs the champion's 225 (dropped 34, added 10) and 246 sentences vs 278
(0.88×) — yet coverage was unchanged, meaning *every key it dropped was spurious*. The
"revise, don't inflate" fence worked: this is a strictly more precise pipeline. But
`eval.py` scores recall only and explicitly does not penalize extra predictions, so
precision is invisible to the score and shows up purely as cost. **A verification tool
cannot pay for itself under a recall-only metric.**

**3. The one thing that did move: variance.** The agentic loop's spread across identical
configs is **sd 0.008 (range 0.020)**, against jun5's champion spread of 0.062 across
three identical runs. Forcing the answer through a schema-checked `submit_annotations`
tool call, rather than parsing JSON out of free prose, appears to remove most of the
run-to-run instability. This is the only result here worth carrying forward — and note it
makes future A/B tests on this bench far cheaper, since a tight sd means fewer replicates
are needed to resolve a real delta.

**4. Guardrail that mattered.** `lookup_term` deliberately returns `hit["name"]` and never
`hit["id"]`, and `submit_annotations` server-side-rejects any key matching `PA\d{5,}`.
`eval.py:normalize_variant` only uppercases non-rsIDs, so a PharmGKB accession
(`PA166153554`) reaching the output keys would silently zero both metrics. Verified clean
on the smoke run. Anyone wiring `tools/term_lookup.py` into the output path must keep this
guard.

## If picking this up again

Don't chase coverage with better extraction — the ceiling analysis says the headroom
isn't there. The unrecovered ~46% of per-variant *meaning* is the real target, and none of
the levers tried across jun4/jun5/jul9 (framing rules, few-shot, ensembling, extended
thinking, agentic tools) have touched it. The `<GENE>*1` wild-type miss class is a pure
prompt/post-processing problem, not a retrieval one — `tools/cross_file.py` already exists
to synthesize those keys and was never wired into the champion.
