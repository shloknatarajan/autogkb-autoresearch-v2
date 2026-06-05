# jun4 — LEARNINGS (running digest)

Run under the **new batch-judge harness** (one judge call per gold variant → fraction
of that variant's gold meaning captured, macro-averaged over variants then papers).
Pipeline model gpt-5.4, judge gpt-5.4-mini, val = 16 papers.

Format: `<label> <meaning_capture>/<variant_coverage> <effect> — lesson`

- **baseline_0cfeeb6** 0.448/0.685 baseline — minimal single-shot prompt. Captures the variants it finds fairly well; misses ~31% of gold variant keys. Re-run scored 0.412 → noise floor ≈ ±0.03.
- **iter1_3877bbb** 0.392/0.865 worse — exhaustive + reciprocal/genotype-enumeration prompt. Coverage soared but macro fell: contradictory directional framings (both "increased" and "decreased") *confuse the per-variant judge*. **Adding contradictory sentences hurts.**
- **iter2_f12b509** 0.401/0.865 worse — exhaustive keys + *terse* sentences. Terseness drops qualifiers (population, comparison group) the judge gives partial credit for. **Don't strip detail.**
- **iter3_72194d0** 0.432/0.820 similar — baseline + regex cross-filing. Within noise; cross-filed sentences mildly dilute already-good variants.
- **iter4_a1252d3** 0.403/0.820 worse — PharmGKB-convention prompt + regex cross-filing. **Key data point:** micro `sentence_coverage` jumped 0.324→0.416 (real, captures more gold meaning) but macro *fell* — regex cross-filing replicates diplotype sentences onto rsIDs/single-allele keys, diluting the **single-sentence variants** that dominate the macro (74/90 dev variants have exactly 1 gold sentence).
- **iter5_c330eda** 0.423/0.697 similar — fill-missing-only cross-filing. Within noise; coverage barely moved.
- **iter6_b292119** 0.414/0.674 similar — framing-quality prompt, no cross-file. Within the noise band [0.412,0.448]: **prompt framing tweaks alone don't move the macro.**
- **iter7_d5528b7** 0.467/0.899 **better (CHAMPION)** — PharmGKB prompt with **model-side cross-filing**: the prompt tells the *model* to file each diplotype/comparison sentence under every constituent allele incl. `<GENE>*1`, use allele/diplotype framing (not `PM/IM`/`AA`), and combine co-reported outcomes. Gets iter4's coverage/micro gains *without* the regex dilution onto rsIDs. Validated by re-run (0.455). **Better, not more: let the model decide where a sentence belongs.**
- **iter8_b62a60d** 0.444/0.888 worse — iter7 + fill-missing regex cross-file. No gain over iter7, adds complexity.
- **iter9_4434788** 0.439/0.899 worse — iter7 + two-pass recall union (cap 5). Second pass dilutes the per-variant judge and doubles cost. **Confirms: adding content hurts macro.**
- **iter10_4cd0ff3** 0.407/0.888 worse — iter7 + per-variant cap=3. Too aggressive: drops gold-matching sentences not in the first 3.

## Cross-cutting lessons for the next run
1. **The batch judge rewards tight, faithful per-variant lists; it punishes added content.** Every "more recall" tactic (exhaustive, reciprocals, regex cross-file, 2nd pass, union) lowered the macro even when micro rose. Optimize *quality per variant*, not volume.
2. **Coverage is nearly free and helps** (missed gold variants score 0), but only via the *model* filing under each allele — not regex replication onto unrelated keys.
3. **The metric is noise-dominated (~±0.03) at 16 papers**, and the LLM judge is itself non-deterministic (no `seed`; re-scoring identical predictions moved 0.467→0.459). Treat single-run deltas < ~0.04 as noise; validate real candidates with a 2nd generation.
4. **Untried lever with real ceiling:** a different/stronger pipeline model. iter7 looks near the practical limit for single-pass gpt-5.4.
