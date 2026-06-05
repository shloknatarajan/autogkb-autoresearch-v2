# jun5 — LEARNINGS (running digest)

Run under the batch-judge harness (one judge call per gold variant → fraction of
that variant's gold meaning captured, macro-averaged over variants then papers).
Pipeline model gpt-5.4, judge gpt-5.4-mini, val = 16 papers. Primary metric:
**meaning_capture** (higher is better).

Format: `<label> <meaning_capture>/<variant_coverage> <effect> — lesson`

- **baseline_ff81a57** 0.414/0.685 baseline — minimal single-shot gpt-5.4. Floor ≈ 0.41. Matches jun4 baseline re-run (0.412) → current eval.py is byte-identical to jun4's; jun4 lessons apply, jun1/jun2 (diff harness) do not. Misses ~31% of gold variant keys (esp. `<GENE>*1`), frames by genotype/metabolizer not star-allele diplotype.
- **iter1_a1c8201** 0.418/0.888 similar — PharmGKB conventions + model-side cross-filing (jun4 iter7 mechanism). Coverage +0.20, micro +0.10, but **macro FLAT** (0.418 vs 0.414). jun4's headline 0.467 was generation noise; the mechanism buys coverage/micro, NOT macro. **Macro lever = per-variant capture quality, not volume.** Building forward on iter1 (free +0.20 coverage).
- **iter2_8abb30e** 0.536/0.888 **better (CHAMPION)** — swap pipeline model gpt-5.4 → **claude-opus-4-8** (same iter1 prompt). Macro **+0.12** vs baseline, far outside noise; beats all of jun4. **Model quality is the dominant macro lever** — with coverage maxed, a stronger model produces faithful per-variant sentences. (Mechanics: `litellm.drop_params=True` + omit `temperature`, deprecated for opus-4-8.) Build all further iters on opus.
- **iter3_5f72105** 0.503/0.865 worse — gemini-2.5-pro pipeline (same prompt). Far above gpt-5.4 (0.418) but below opus champion (0.536) by ~1 noise unit. Both frontier models >> gpt-5.4; task is model-bound (model gap +0.12 ≫ any prompt tweak ±0.03). opus stays champion. Stop swapping models; push opus.
- **iter4_f72fda8** 0.458/0.876 worse — opus + extended thinking (reasoning_effort=high). −0.078 vs plain opus; micro 0.513→0.400. Thinking makes opus more conservative / reframes away from gold's literal PharmGKB phrasing. **Keep plain single-pass opus, no reasoning.** "More compute" hurts the per-variant macro (consistent across 2-pass, capping, thinking).
- **iter5_69db5ac** 0.529/0.899 similar — opus + rsID-genotype & HLA framing rules (driven by a dev miss diagnostic: top miss class = rsIDs framed by nucleotide genotype "Genotypes CT+TT ... as compared to genotype CC"). Flat vs champion 0.536, micro slightly down. Prompt elaboration on opus stays in noise (same as on gpt-5.4). Champion stays iter2 (simpler).
- **iter6_3a81462** 0.409/0.708 worse — ABLATION: opus + MINIMAL prompt = 0.409 (≈ gpt-5.4 baseline), coverage collapses 0.888→0.708. **Key finding: it's an INTERACTION.** Rich prompt buys opus +0.13 but bought gpt-5.4 ~0 (iter1). Champion needs BOTH a capable model (opus) AND the rich PharmGKB prompt — opus can execute the conventions (list every variant incl *1, cross-file, allele framing) that gpt-5.4 couldn't. (Corrects iter3's "model-bound not prompt-bound".)
- **iter7_b086029** 0.495/0.876 worse — few-shot worked output mapping on opus. −0.041; concrete exemplar narrows opus output vs the abstract conventions. Champion stays iter2. (Theme: opus + abstract rich prompt = sweet spot; extra constraints/content all ≤ it.)

