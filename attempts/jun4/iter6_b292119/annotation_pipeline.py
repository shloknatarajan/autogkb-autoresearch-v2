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

MODEL = "gpt-5.4"

SYSTEM_PROMPT = """You are a PharmGKB curator. You read the full text of a \
pharmacogenomics paper (in markdown) and extract its variant annotations.

Produce a JSON object "variant_sentences" mapping each genetic variant discussed
in the paper to the list of standardized PharmGKB association sentences that
assert an association about that variant.

  - Keys: variant identifiers in canonical form -- rsIDs (e.g. "rs9923231") or
    star/HLA alleles (e.g. "CYP2C19*2", "HLA-B*15:01"). A variant with no reported
    association still appears as a key mapped to an empty list [].
  - Values: a list of standardized association sentences for that variant. Write
    them in PharmGKB house style:
      * ALLELE/DIPLOTYPE framing for star-allele genes -- use star alleles and
        diplotypes (e.g. "CYP2D6 *3/*3 + *4/*4", "UGT1A1 *6 + *28"), NOT
        nucleotide genotypes ("AA"/"GA") and NOT metabolizer labels ("PM/IM",
        "poor metabolizer"); translate to the underlying alleles when the paper
        reports by genotype letters or metabolizer status.
      * ONE sentence per distinct association. When the paper reports several
        outcomes together for the same group, COMBINE them into one sentence
        (e.g. "Neutropenia, Leukopenia or Diarrhea") rather than splitting into
        near-duplicates. Do not pad with reciprocal restatements or genotype
        groups the paper never discusses.
    Each sentence states the variant/genotype, polarity ("is" / "is not
    associated"), direction ("increased" / "decreased") when applicable, the
    phenotype or clinical outcome (use the paper's outcome terms), the drug (when
    relevant), and the comparison group when stated. Example style:
     "CYP2C19 *1/*2 + *2/*2 is not associated with increased likelihood of Major
      Adverse Cardiac Events when treated with clopidogrel as compared to CYP2C19 *1/*1."

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


def predict(markdown_content):
    resp = litellm.completion(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": markdown_content},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = _extract_json_object(resp.choices[0].message.content or "")
    vs = data.get("variant_sentences", {})
    if not isinstance(vs, dict):
        vs = {}
    variant_sentences = {str(k): (v or []) for k, v in vs.items()}
    return {"variant_sentences": variant_sentences}
