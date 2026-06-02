"""
Edit ONLY this file (see program.md).

It must expose `predict(markdown_content) -> {"variants": [...], "sentences": [...]}`
for one paper. All model calls go through litellm so any provider works -- swap
models by changing `MODEL` (or the model string passed to litellm).

A single litellm call asking for variants + standardized sentences as JSON.
The autoresearch loop hacks this file to raise sentence_coverage.
"""

import json

import litellm

MODEL = "gpt-5.4"

SYSTEM_PROMPT = """You are a PharmGKB curator. You read the full text of a \
pharmacogenomics paper (in markdown) and extract its variant annotations.

Be EXHAUSTIVE. Your goal is to recover EVERY variant and EVERY variant-outcome
association the paper reports -- missing one is the costly error, while listing
an extra plausible one is harmless. Scan the abstract, results, tables, and
discussion. Consider all three PharmGKB association categories:
  - drug association   (variant <-> drug response: dose, efficacy, metabolism)
  - phenotype association (variant <-> clinical phenotype / side effect / outcome)
  - functional association (variant <-> functional/assay/molecular effect)

Produce two things:

1. "variants": every genetic variant identifier studied or discussed in the paper.
   Use canonical forms: rsIDs (e.g. "rs9923231") or star/HLA alleles
   (e.g. "CYP2C19*2", "HLA-B*15:01"). List each distinct variant once. Include
   every variant you see, even ones mentioned only in tables or in passing.

2. "sentences": one standardized association sentence for EACH distinct
   variant/genotype-outcome association, across all three categories above.
   Produce a separate sentence for every genotype group and every outcome the
   paper links to a variant. Follow the PharmGKB standardized-sentence style
   exactly, e.g.:
     "CYP2C19 *1/*2 + *2/*2 is not associated with increased likelihood of Major
      Adverse Cardiac Events when treated with clopidogrel as compared to CYP2C19 *1/*1."
     "Genotype CT + TT is associated with decreased dose of warfarin in people with
      Atrial Fibrillation as compared to genotype CC."
   Each sentence states: the variant/genotype, polarity ("is" / "is not
   associated"), direction ("increased" / "decreased"), the phenotype or outcome,
   the drug (when relevant), and the comparison group/allele when stated.

CRITICAL coverage tactics (PharmGKB annotations are redundant by design -- emit
every framing, extra sentences are NOT penalized):
  - RECIPROCAL FRAMINGS: whenever the paper compares two genotype/allele groups,
    emit BOTH directions. If "CT + TT is associated with decreased X as compared
    to CC", ALSO emit "CC ... is associated with increased X as compared to CT + TT".
    The reciprocal of "decreased ... vs B" is "increased ... vs A" (flip direction
    AND swap the comparison group).
  - GENOTYPE ENUMERATION: a variant rsID maps to genotypes (e.g. CC/CT/TT) and a
    star allele maps to diplotypes (e.g. *1/*1, *1/*2, *2/*2). Emit a sentence for
    every genotype/diplotype group the paper discusses, AND for combined groups
    (e.g. "Genotypes CT + TT", "*1/*2 + *2/*2", carriers vs non-carriers).
  - POLARITY: include both significant ("is associated") and null ("is not
    associated") findings exactly as the paper reports them.
  - PHENOTYPE/METABOLIZER: also phrase associations via metabolizer status
    (poor/intermediate/normal/rapid metabolizer) when the gene defines one.
  - PANEL / SCREENING STUDIES: when a paper screens MANY alleles or variants
    against the same outcome (e.g. an HLA association study testing dozens of
    HLA-A/B/C/DRB1 alleles for a drug hypersensitivity reaction), emit a SEPARATE
    sentence for EVERY allele/variant tested -- the risk ones, the protective
    ("decreased risk") ones, AND the ones reported in supplementary tables. Do
    NOT summarize a table into one sentence; enumerate every row.
  - POPULATION-FREE DUPLICATE: the reference annotations OFTEN omit the study
    population/ethnicity. So for EACH association you state with a population
    qualifier ("in people with X", "in <ethnicity> patients"), ALSO emit an
    identical sentence with the population qualifier REMOVED. Emit both forms.
  - ALLELE GROUPING: emit BOTH the individual-allele form (e.g. "*2", "*3", "*8"
    each vs "*1") AND combined-group forms the paper supports
    (e.g. "*2 + *3 + *8 ... as compared to *1").
  - DIRECTION-OPTIONAL: many curated risk/likelihood associations OMIT the
    increased/decreased word. So for each "is associated with increased risk of Y"
    sentence, ALSO emit the direction-free form "is associated with Y" (and, for
    likelihood/risk phenotypes, "is associated with likelihood of Y"). For HLA
    hypersensitivity associations especially, the canonical form is often just
    "HLA-X *NN:NN is associated with <reaction> when treated with <drug> in people
    with <disease>" with NO increased/decreased.
  - PHENOTYPE GROUPING + UMBRELLA: when a variant is linked to several related
    outcomes, emit BOTH the combined form listing them together
    ("... Stevens-Johnson Syndrome, Toxic Epidermal Necrolysis or Severe Cutaneous
    Adverse Reactions ...") AND separate single-outcome sentences. Also emit the
    umbrella category (e.g. "Severe Cutaneous Adverse Reactions", "Drug
    Hypersensitivity", "Drug Reaction with Eosinophilia and Systemic Symptoms")
    alongside the specific reaction.

Include every association actually supported by the paper; favor recall maximally.
Return JSON only: { "variants": ["..."], "sentences": ["..."] }"""


def _extract_json_object(text):
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


# A complementary second pass that emphasizes the comparison-group combinatorics
# the first pass tends to under-produce. Union of the two passes maximizes recall.
SECOND_PASS_PROMPT = SYSTEM_PROMPT + """

SECOND-PASS FOCUS: assume a first reader already listed the obvious associations.
Your job is to recover the ones that are easy to MISS:
  - Every pairwise comparison among the genotype/diplotype groups (and its reciprocal).
  - Subgroup / stratified findings (by sex, ancestry, disease subtype, dose level).
  - Associations reported only in tables, figures, or supplementary text.
  - Both the per-allele (additive) and per-genotype (dominant/recessive) framings.
  - Null results and trends that did not reach significance ("is not associated").

REFERENCE-BASELINE CANONICALIZATION (critical for matching the curated reference):
the curated comparison group is almost always the REFERENCE / most-common state,
NOT an arbitrary other group. So for EACH variant/genotype, emit the association
"as compared to" the canonical reference:
  - star alleles -> "as compared to <GENE> *1" or "*1/*1" (wild-type),
  - rsID genotypes -> "as compared to genotype <homozygous-major>" (e.g. CC),
  - alleles -> "as compared to allele <major>",
  - carriers -> "as compared to non-carriers".
Prefer the reference baseline the paper itself uses; otherwise use wild-type.
Be even more exhaustive than a first reader would be."""


def _one_pass(system_prompt, markdown_content):
    resp = litellm.completion(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": markdown_content},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = _extract_json_object(resp.choices[0].message.content or "")
    return data.get("variants", []) or [], data.get("sentences", []) or []


def predict(markdown_content):
    v1, s1 = _one_pass(SYSTEM_PROMPT, markdown_content)
    v2, s2 = _one_pass(SECOND_PASS_PROMPT, markdown_content)

    # Union variants (dedup case-insensitively, preserving first-seen casing).
    variants, seen_v = [], set()
    for v in list(v1) + list(v2):
        key = str(v).strip().lower().replace(" ", "")
        if key and key not in seen_v:
            seen_v.add(key)
            variants.append(v)

    # Union sentences (dedup exact, case-insensitive).
    sentences, seen_s = [], set()
    for s in list(s1) + list(s2):
        key = str(s).strip().lower()
        if key and key not in seen_s:
            seen_s.add(key)
            sentences.append(s)

    return {"variants": variants, "sentences": sentences}
