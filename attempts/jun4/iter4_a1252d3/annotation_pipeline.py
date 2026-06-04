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
pharmacogenomics paper (in markdown) and extract its variant annotations into the
exact PharmGKB standardized-sentence conventions.

Produce a JSON object "variant_sentences" mapping each genetic variant discussed
in the paper to the list of standardized PharmGKB association sentences that
assert an association about that variant.

KEYS -- list EVERY variant the paper studies or mentions (even ones only in a
table or in passing), in canonical form: rsIDs (e.g. "rs9923231") or star/HLA
alleles (e.g. "CYP2C19*2", "HLA-B*15:01"). For star-allele genes also include the
wild-type reference allele "<GENE>*1" as a key. A variant with no reported
association still appears as a key mapped to an empty list [].

VALUES -- follow PharmGKB conventions EXACTLY. These conventions are how the gold
is written; matching them is what counts:
  - ALLELE/DIPLOTYPE FRAMING, not genotype letters or metabolizer labels. For a
    star-allele gene, phrase the association with star alleles and diplotypes
    (e.g. "CYP2D6 *3/*3 + *4/*4", "UGT1A1 *6 + *28"), NOT nucleotide genotypes
    ("AA"/"GA") and NOT metabolizer phenotypes ("PM/IM", "poor metabolizer"). If
    the paper reports by metabolizer status or genotype letters, translate to the
    underlying alleles/diplotypes.
  - FILE UNDER EVERY CONSTITUENT ALLELE. An association about a diplotype or an
    allele comparison is filed under EACH star allele it names AND under the
    comparison allele, including the "<GENE>*1" reference. e.g. an association
    "CYP2D6 *3/*3 + *4/*4 ... as compared to CYP2D6 *1/*1" is filed under
    CYP2D6*3, CYP2D6*4 AND CYP2D6*1 -- the identical sentence under each key.
  - COMBINE co-reported outcomes the way the paper groups them into ONE sentence
    (e.g. "Neutropenia, Leukopenia or Diarrhea"); do not split one finding into
    many near-duplicate sentences.
  - Each sentence states the allele/diplotype, polarity ("is" / "is not
    associated"), direction ("increased"/"decreased") when applicable, the
    phenotype or clinical outcome (use the paper's outcome terms), the drug (when
    relevant), and the comparison group ("as compared to ...") when stated.
  - List ONLY associations the paper actually asserts. Do NOT invent reciprocal
    restatements or enumerate genotype groups the paper never discusses.

Example style:
   "CYP2C19 *1/*2 + *2/*2 is not associated with increased likelihood of Major
    Adverse Cardiac Events when treated with clopidogrel as compared to CYP2C19 *1/*1."
   "UGT1A1 *6 is associated with increased severity of Neutropenia, Leukopenia or
    Diarrhea when treated with irinotecan in people with Stomach Neoplasms as
    compared to UGT1A1 *1."

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

    # Cross-file: PharmGKB gold files each association under EVERY variant it
    # names (constituent + comparison alleles, incl. <GENE>*1). meaning_capture
    # is macro per variant, so replicate each star/HLA sentence under every
    # allele in its text. Deterministic, no extra model calls.
    variant_sentences = cross_file_sentences(variant_sentences)

    return {"variant_sentences": variant_sentences}
