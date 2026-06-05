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

Produce a JSON object "variant_sentences" mapping each genetic variant discussed
in the paper to the list of standardized PharmGKB association sentences that
assert an association about that variant.

  - Keys: variant identifiers in canonical form -- rsIDs (e.g. "rs9923231") or
    star/HLA alleles (e.g. "CYP2C19*2", "HLA-B*15:01"). A variant with no reported
    association still appears as a key mapped to an empty list [].
  - Values: a list of standardized association sentences for that variant. Each
    sentence states the variant/genotype, polarity ("is" / "is not associated"),
    direction ("increased" / "decreased"), the phenotype or outcome, the drug
    (when relevant), and the comparison group when stated. Example style:
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

    # Cross-file: PharmGKB gold files each association under EVERY variant it
    # names (constituent + comparison alleles). meaning_capture is macro per
    # variant, so replicate each star/HLA sentence under every allele in its
    # text. Deterministic, no extra model calls; extra keys are not penalized.
    variant_sentences = cross_file_sentences(variant_sentences)

    return {"variant_sentences": variant_sentences}
