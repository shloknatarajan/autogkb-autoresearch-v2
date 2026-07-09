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

**1. [CORRECTED — see below] The agent was handed 6 of its 10 missed variants and
discarded them.**

Original claim, written before the val failure analysis: *"the recall tools were built to
fix a problem regex structurally cannot fix,"* on the basis that `extract_all_variants()`
has a **dev** ceiling of 80/90 = 0.889 against dev gold — apparently exactly where the
pipeline sat.

**That inference was wrong.** The dev ceiling is not the binding constraint, and I
generalized a dev number onto val. A per-gold-sentence failure analysis on val
(`analysis/failure_analysis.html`, owner waived the val-gold rule for it) shows:

| | |
|---|---|
| regex-alone ceiling, **val** | 83/89 = **0.933** (not 0.889) |
| agentic pipeline, as shipped | 79/89 = 0.888 |
| agentic **∪ regex candidates** | 85/89 = **0.955** |

The agent called `extract_variants` on all 16 papers, and on two of them the tool
**returned gold variants that the model then dropped from its answer**: `CYP2D6*1xN`,
`*2xN`, `*4xN` (PMC6435416) and `HLA-B*35:01`, `HLA-C*04:01`, `HLA-DRB1*01:01`
(PMC3387531). Unioning the tool's candidates into the output keys is free, deterministic,
and worth **+0.067 coverage**.

The cause is a line in the WORKFLOW section of the system prompt in this very attempt:
*"ignore what it found that the paper does not really discuss."* The model obeyed it. The
tool was not the bottleneck; **trust in the tool** was.

What *is* structurally unreachable by regex is narrower than claimed: wild-type
`<GENE>*1` keys the paper never writes (`CYP2B6*1`), and the metabolizer-phenotype keys
in finding 5 below.

**Real lesson:** compute a recall tool's offline ceiling *on the split you will report on*,
then compare it to `prediction ∪ tool` — not to the tool alone. And never tell an agent to
second-guess a deterministic tool you trust more than the model.

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

**5. The prompt forbids the gold answer on one paper (n=3 sentences, capture 0.000).**
PMC10880264's gold keys include `CYP2C19 intermediate metabolizer` and `CYP2D6 poor
metabolizer` — metabolizer *phenotypes* used as variant keys. The champion prompt (and
therefore this one) says: *"NOT metabolizer labels ('PM/IM', 'poor metabolizer');
translate to the underlying alleles/diplotypes."* Both pipelines obey and both score
**0.000** on all three of that paper's gold sentences. The rule is correct for most
papers and catastrophically wrong for this one. This is a prompt bug with a guaranteed
fix, and it is the only such failure class on the bench.

**6. Where the score actually goes (val, 101 gold sentences, per-sentence judge).**

| failure mode | champion | agentic |
|---|---|---|
| OK (capture ≥ 0.75) | 42 | 39 |
| QUALIFIER_DROPPED (0.25–0.75) | 30 | 32 |
| SENTENCE_LOST (< 0.25, variant found) | 18 | 19 |
| MISSING_VARIANT | 11 | 11 |

The dominant failure is **not extraction** — it is writing the sentence correctly once the
variant is already in hand. Every tool built in jul9 targets the smallest bucket (11).
Mean capture by variant class shows star alleles as the bottleneck: **0.509 champion /
0.486 agentic across n=49 gold sentences**, half the bench.

**7. `tools/regex_variants.py` has a live trap.** `extract_all_variants()` correctly
returns `CYP2D6*4xN`, but its sibling `normalize_star_allele()` *strips* the copy-number
suffix (`CYP2D6*4xN → CYP2D6*4`). Wiring the normalizer into the output path would
silently destroy exactly the keys finding 1 says to recover.

## If picking this up again

Ranked by expected value per dollar, from the val failure analysis:

1. **Union the regex candidates into the output keys.** +0.067 coverage (0.888 → 0.955),
   zero API cost, deterministic. Delete the "ignore what it found" line from the prompt.
2. **Carve out the metabolizer-phenotype exception** in the prompt. 3 gold sentences at
   0.000 today; a bounded, guaranteed fix.
3. **Wire in `tools/cross_file.py`** for the `<GENE>*1` wild-type keys regex cannot see.
   Free, already written, never used by the champion.
4. **Then, and only then, attack qualifier loss** — the ~30-sentence QUALIFIER_DROPPED
   bucket is the largest and the least understood. No lever tried across jun4/jun5/jul9
   has moved it. Note the tooling here targets *finding* variants; nothing yet helps the
   model *write the sentence*.

Do **not** spend more on extraction tooling or on "more compute" (more turns, planning,
subagents) — that intervention has now been falsified five separate times on this bench.

One methodological gain worth keeping regardless of the above: the schema-enforced
`submit_annotations` tool collapsed run-to-run variance to sd 0.008 (from jun5's 0.062
spread), which means ±0.02 effects are now resolvable with three runs. Several of jun5's
"flat" and "regressed" verdicts were measured against noise nearly as large as the effect
and are not actually established.
