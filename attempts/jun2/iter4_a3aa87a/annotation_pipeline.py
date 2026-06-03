"""
Edit ONLY this file (see program.md).

It must expose `predict(markdown_content) -> {"variant_sentences": {variant: [...]}}`
for one paper: a mapping from each variant to the standardized association
sentences asserting an association about that variant. All model calls go through
litellm so any provider works -- swap models by changing `MODEL`.

BASELINE: a single straightforward litellm call asking for the variant -> sentence
mapping as JSON. The autoresearch loop hacks this file to raise meaning_capture.
"""

import json

import litellm

from tools.cross_file import cross_file_sentences

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

Produce a JSON object "variant_sentences" mapping EACH variant to the list of
standardized association sentences that assert an association ABOUT that variant:

  - Keys: every genetic variant identifier studied or discussed in the paper, in
    canonical form -- rsIDs (e.g. "rs9923231") or star/HLA alleles (e.g.
    "CYP2C19*2", "HLA-B*15:01"). Include every variant you see, even ones
    mentioned only in tables or in passing. A variant with no association still
    gets a key mapped to an empty list [].
  - Values: a list of standardized association sentences for that variant -- one
    for EACH distinct genotype-group / outcome association the paper links to it,
    across all three categories above. File each sentence under the variant whose
    genotype/allele it is about (e.g. a sentence about genotypes "CT + TT" of
    rs9923231 goes under "rs9923231"; if an association is about a diplotype
    combining alleles, file it under each constituent star-allele variant).
    Follow the PharmGKB standardized-sentence style exactly, e.g.:
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

Include every association actually supported by the paper; favor recall maximally.
Return JSON only: { "variant_sentences": { "<variant>": ["<sentence>", ...], ... } }"""


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
SECOND_PASS_PROMPT = (
    SYSTEM_PROMPT
    + """

SECOND-PASS FOCUS: assume a first reader already listed the obvious associations.
Your job is to recover the ones that are easy to MISS:
  - Every pairwise comparison among the genotype/diplotype groups (and its reciprocal).
  - Subgroup / stratified findings (by sex, ancestry, disease subtype, dose level).
  - Associations reported only in tables, figures, or supplementary text.
  - Both the per-allele (additive) and per-genotype (dominant/recessive) framings.
  - Null results and trends that did not reach significance ("is not associated").
Be even more exhaustive than a first reader would be."""
)


def _one_pass(system_prompt, markdown_content):
    """Return one pass's {variant -> [sentences]} mapping (raw, un-merged)."""
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
    vs = data.get("variant_sentences", {})
    if not isinstance(vs, dict):
        return {}
    return {str(k): (v or []) for k, v in vs.items()}


def _variant_key(v):
    """Case/space-insensitive key for deduping variant ids across passes."""
    return str(v).strip().lower().replace(" ", "")


def predict(markdown_content):
    # Two-pass ensemble: union the per-variant sentence groups from both passes to
    # maximize recall.
    g1 = _one_pass(SYSTEM_PROMPT, markdown_content)
    g2 = _one_pass(SECOND_PASS_PROMPT, markdown_content)

    variant_sentences = {}
    key_to_canonical = {}
    for group in (g1, g2):
        for variant, sents in group.items():
            vkey = _variant_key(variant)
            if not vkey:
                continue
            canonical = key_to_canonical.setdefault(vkey, variant)
            bucket = variant_sentences.setdefault(canonical, [])
            seen = {s.strip().lower() for s in bucket}
            for s in sents:
                s = str(s)
                if s.strip() and s.strip().lower() not in seen:
                    seen.add(s.strip().lower())
                    bucket.append(s)

    # Cross-file: PharmGKB gold files each association under EVERY variant it
    # names (constituent + comparison alleles). meaning_capture is macro per
    # variant, so replicate each star/HLA sentence under every allele in its
    # text. Deterministic, no extra model calls; extra keys are not penalized.
    variant_sentences = cross_file_sentences(variant_sentences)

    return {"variant_sentences": variant_sentences}
