# Annotation Ambiguity & Metric Fairness

*Are the current metrics (`variant_coverage` + per-variant `meaning_capture`) a fair
test of an agent's ability to **reproduce PharmGKB annotations** for a paper?*

**Short answer: partially.** The metrics fairly measure *"did you recover the biology
the way PharmGKB happened to write it down."* They do **not** fairly measure *"did you
extract a correct, faithful set of associations,"* because the gold encodes **one
particular curation convention** among several that are all defensible — and both the
key-matching and the macro-averaging punish an agent for choosing a different-but-correct
structure. This document catalogs where that happens, grounded in the actual gold data
(`benchmarks/sentence_bench_by_variant.jsonl`, 32 papers / 179 variant keys / 210 cross-filed sentences).

---

## How scoring works (recap)

For each paper the gold is `{variant -> [standardized association sentences]}`. Two recall metrics:

- **`variant_coverage`** — fraction of gold variant *keys* recovered, after `normalize_variant`
  (rsID → `rs<digits>`; everything else → uppercased, whitespace-stripped). Exact-key set match.
- **`meaning_capture`** (primary) — for each gold variant, an LLM judge scores 0–1 how much of
  that variant's gold sentences are captured by the agent's sentences *filed under that same key*.
  **Macro-averaged**: every variant key counts equally, then every paper counts equally.

The structural fact that drives most of the unfairness below:

> **The agent is scored per gold variant key.** A meaning it expresses under the "wrong" key
> earns **zero** on the key the gold expected — even if the meaning is present and correct
> somewhere in the output. The LLM judge is lenient about *wording*; the harness is rigid about
> *where a meaning is filed and what the key is called*.

Distribution of the gold (for calibration):

| | count |
|---|---|
| variant keys total | 179 (mean 5.6/paper, max 43) |
| → rsID keys | 48 |
| → star-allele keys | 71 (incl. **13 `*1` reference-allele** keys) |
| → HLA keys | 58 |
| keys with exactly 1 gold sentence | 148 / 179 (83%) |
| sentences cross-filed under >1 key | 22 (avg **4.2** keys each, max 15) |
| sentences joining multiple diplotypes with `+` | 55 |

---

## The core tension

PharmGKB gold is a **downstream curation artifact**, not a transcription of the paper. Compare the
*raw* PharmGKB sentence to the *gold* (standardized) sentence for the same finding in PMC3548984:

- **Raw annotation:** *"Genotype PM/PM is associated with increased odds of a disease event during
  tamoxifen treatment as compared to EM/EM."* (keyed by metabolizer phenotype, outcome = "disease event")
- **Gold (scored):** *"CYP2D6 \*3/\*3 + \*4/\*4 are associated with increased likelihood of
  **Recurrence** when treated with tamoxifen in women with Breast Neoplasms as compared to CYP2D6 \*1/\*1."*
  (keyed by enumerated star diplotypes, outcome = "Recurrence", population added)

Both describe the same result. The gold version reflects curator choices — *translate PM/PM to the
specific diplotypes, rename the outcome to a controlled-vocabulary term, enumerate the comparison,
attach the population.* An agent reading **only the paper markdown** has no way to know that *this
paper's* "disease event" was normalized to "Recurrence", or exactly which diplotypes the curator chose
to enumerate. It is being asked to reproduce decisions made with information and conventions it cannot see.

---

## Catalog of legitimate ambiguity

Each item: the curation choice · a real gold example · why an alternative is equally correct · how the
current metric penalizes the alternative · severity.

### 1. Variant key granularity — allele vs diplotype vs genotype vs metabolizer vs rsID
**Severity: HIGH (the dominant unfairness).**

The same association can be keyed at several levels. Gold keys star-gene findings by **individual
allele**, and files one diplotype sentence under *each* constituent allele:

```
PMC3548984: "CYP2D6 *3/*3 + *4/*4 are associated with increased likelihood of Recurrence ..."
  is filed under THREE keys:  CYP2D6*1, CYP2D6*3, CYP2D6*4
```

Equally correct structurings an agent might produce:
- key by **diplotype**: `"CYP2D6*3/*4"` → one entry
- key by **metabolizer phenotype**: `"CYP2D6 PM"` (how the paper actually reports it)
- key by **rsID**: the alleles' defining SNPs

**Penalty:** all of these score **0** on the gold's `*1`/`*3`/`*4` keys → `variant_coverage` collapses
and every missed key contributes a 0 to the macro `meaning_capture`. The agent had the biology exactly
right and is scored as if it found nothing. Because the same sentence is cross-filed under avg **4.2**
keys (max 15), choosing the "wrong" granularity is penalized **4× over**.

### 2. The cross-filing convention itself
**Severity: HIGH.**

Gold's rule — *file each diplotype/comparison sentence under every constituent allele, including the
reference* — is a PharmGKB house style, learned by jun4/jun5 only by reverse-engineering the gold (the
"model-side cross-filing" prompt). It is not a fact about the paper. An agent that files each association
once, under its most specific key, is arguably **more** correct (no redundancy) but scores far worse,
because the macro denominator counts every cross-filed key as a separate variant to satisfy.

### 3. The `*1` reference allele as a "variant studied"
**Severity: HIGH.** 13 of 179 keys are `<GENE>*1`.

`CYP2D6*1` is the **wild-type reference** — not a variant in any normal sense, but gold lists it as a key
populated with the comparison ("...as compared to CYP2D6 \*1/\*1") sentences. A reasonable agent would not
report "the normal allele" as a finding. jun4's miss diagnostic found `*1` keys were the single largest
miss class. Penalizing their absence tests knowledge of PharmGKB convention, not extraction skill.

### 4. rsID ↔ star-allele identity collapse
**Severity: MEDIUM–HIGH.**

`normalize_variant` maps rsIDs and star alleles into **disjoint** namespaces: `CYP2C19*2` and its
defining SNP `rs4244285` are the *same biological variant* but never match. The gold's choice of which
representation to use is arbitrary per paper (papers often report both). An agent that picks the other
valid identifier scores 0 on coverage and meaning for that variant. 48 rsID + 71 star keys means this
fault line runs through the whole bench.

### 5. Phenotype / outcome term choice
**Severity: MEDIUM–HIGH.**

The judge is explicitly strict that *"a different phenotype substituted"* → 0. But gold's phenotype is a
**controlled-vocabulary rename** of the paper's wording ("disease event" → "Recurrence"; "MACE" →
"Major Adverse Cardiac Events"; "neutropenia" grouped as "Neutropenia, Leukopenia or Diarrhea"). An
agent that uses the paper's own faithful term, or a clinically-equivalent synonym the judge doesn't
recognize as the same, loses credit for a correct extraction. This is a vocabulary-normalization test
smuggled into a meaning test.

### 6. Comparison group inclusion & choice
**Severity: MEDIUM.**

88 of 210 sentences carry "as compared to ...". Whether to state a comparison, and which baseline to name
(`*1/*1`? the major-allele genotype? "non-carriers"?), is a framing choice. The judge gives only *partial*
credit when "an important comparison ... is missing", so an agent that omits a comparison the paper left
implicit is docked for a stylistic omission, not a wrong claim.

### 7. Sentence grouping vs splitting (both directions)
**Severity: MEDIUM.**

Gold **merges** in two ways the agent must guess:
- multiple diplotypes into one sentence with `+` (55 sentences: `"*1/*3 + *1/*4 + *1/*6 are not associated..."`)
- multiple outcomes into one (88 sentences: `"...Neutropenia, Leukopenia or Diarrhea"`)

The judge tolerates merge/split *within a key*, which helps — but the **grouping decision determines key
membership** (which diplotypes get bucketed as "associated" vs "not associated"), and *that* is scored
rigidly per key. Two curators could bucket borderline diplotypes differently from the same paper.

### 8. Polarity bucketing of grouped genotypes
**Severity: MEDIUM.**

Gold splits diplotypes into an "is associated" group and an "is not associated" group based on which the
paper found significant (`*3/*3 + *4/*4` associated; `*1/*3 + *1/*4 + *1/*6` *not*). The significance
cutoff and which genotypes were "tested" is a judgment call; a different but defensible reading produces
different buckets, and polarity reversal is a hard 0 from the judge.

### 9. Direction-of-effect optionality
**Severity: LOW–MEDIUM.**

The raw schema has a "Direction of effect" field that is sometimes empty. Gold sometimes states
increased/decreased and sometimes doesn't. An agent that adds a correct direction the gold omitted (or
omits one gold states) can be marked partially wrong on a detail the source itself treats as optional.

### 10. Metabolizer → diplotype enumeration requires outside knowledge
**Severity: MEDIUM.**

To turn "PM/PM" into "CYP2D6 \*3/\*3 + \*4/\*4 + ..." the agent must know the **full allele-to-phenotype
mapping** and which diplotypes to enumerate — a star-allele function table that lives in PharmGKB/CPIC
reference data, not the paper. Which diplotypes the curator chose to list is not derivable from the text.

### 11. Source material beyond the markdown
**Severity: HIGH where it occurs (capability, not structure).**

Some gold associations come from **supplementary tables** (there is a `tools/fetch_supplements.py` /
`refresh_bench_supplements.py` for exactly this). If a gold sentence's variant or finding only appears in
a supplement not present in `markdown_content`, the agent **cannot** reproduce it at any skill level. This
isn't ambiguity — it's an unrecoverable gold item that drags the recall metric down for reasons unrelated
to the agent. Worth auditing how many gold items are markdown-absent.

---

## Measurement issues that compound the unfairness

These aren't structural ambiguity but they decide whether the metric can even *see* a real improvement:

- **Macro over single-sentence keys + small N.** 83% of keys have exactly 1 gold sentence, and each is
  near-binary (captured or not). With only 16 val papers, a few all-or-nothing key matches swing the mean.
- **Judge noise ±0.05.** jun5 ran identical champion code three times: 0.536 / 0.598 / 0.540. Several
  "regressions" were inside this band. Any structural-fairness fix smaller than ~0.06 is invisible.
- **The macro multiplier on cross-filing.** Because one sentence maps to avg 4.2 keys, a single
  granularity disagreement (item 1) moves the macro 4× as much as a genuinely independent miss — so the
  metric is most sensitive exactly where it is least fair.

---

## Verdict: which gaps are *unfair* vs *acceptable*

| Ambiguity | Tests extraction skill? | Verdict |
|---|---|---|
| 1. Key granularity | No — tests PharmGKB convention | **Unfair** |
| 2. Cross-filing rule | No — house style | **Unfair** |
| 3. `*1` reference key | No — convention | **Unfair** |
| 4. rsID ↔ star identity | No — arbitrary representation | **Unfair** |
| 5. Phenotype renaming | Partly — vocab normalization | **Mostly unfair** |
| 6. Comparison framing | Partly | Borderline |
| 7. Grouping/splitting | Partly | Borderline |
| 8. Polarity bucketing | Yes — this is real biology | **Fair** |
| 9. Direction optionality | Mostly yes | Mostly fair |
| 10. Metabolizer→diplotype | No — needs external table | **Unfair** |
| 11. Beyond-markdown gold | No — unrecoverable | **Unfair (broken item)** |

The metric is **fair on the things that should be hard** (polarity, direction, getting the actual
association right — items 8–9, and the judge's strictness there is correct). It is **unfair on
representation/convention** (items 1–5, 10–11), which is where most of the current score gap lives —
and which is *not* what "can an agent reproduce the annotations" should mean if we care about correct
biology rather than house style.

---

## Recommendations (to make the metric measure extraction, not convention)

1. **[IMPLEMENTED 2026-06-23]** **Score meaning at the paper level, not per gold key, for the primary
   metric.** Match each gold sentence to the agent's *best* sentence anywhere in its output
   (judge-assisted), independent of which key it's filed under. This removes items 1–4 at a stroke while
   keeping the judge's strictness on polarity/direction/phenotype. Keep per-key meaning_capture as a
   secondary "convention adherence" score. → `eval.py`: `paper_meaning_capture()`; primary
   `meaning_capture`, secondary `meaning_capture_perkey`. *Validation:* gold content filed under a
   deliberately wrong key scores **1.000** on the new primary vs **0.000** on the old per-key metric.
2. **[IMPLEMENTED 2026-06-23]** **Unify rsID ↔ star-allele identity** in coverage matching via a mapping
   table (item 4), accepting either representation as a key match. → `eval.py`:
   `STAR_ALLELE_DEFINING_RSID` (curated, single-defining-SNP only) + `variant_coverage_match()` (bipartite,
   so one prediction can't cover two distinct gold keys that share a defining SNP). Primary
   `variant_coverage`, secondary `variant_coverage_strict`.
3. **Exclude (or separately report) `*1` reference-allele keys** (item 3) — don't require reporting the wild type.
4. **Make phenotype matching synonym-aware** (item 5): let the judge map clinically-equivalent terms, or
   provide the controlled term as context so the test is "same outcome?" not "same string?".
5. **Audit gold items that are unrecoverable from `markdown_content`** (item 11) and either supply the
   supplement to the agent or drop those items from scoring.
6. **Report mean ± std over ≥3 generations** as the standard unit (the noise is ±0.05); stop treating
   single-run deltas under ~0.06 as signal.

A defensible reframing: keep **two** scores — a **biology score** (paper-level, representation-invariant,
strict on polarity/direction/outcome) as the headline, and a **convention score** (today's per-key macro)
as a secondary "matches PharmGKB house style" measure. Right now the two are conflated, and the convention
half is doing more of the work than it should.
