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

# Anthropic doesn't take OpenAI's response_format=json_object verbatim; drop unsupported
# params so the same predict() body works across providers (we parse JSON from the text).
litellm.drop_params = True

MODEL = "anthropic/claude-opus-4-8"

SYSTEM_PROMPT = """You are a PharmGKB curator. You read the full text of a \
pharmacogenomics paper (in markdown) and extract its variant annotations.

Produce a JSON object "variant_sentences" mapping each genetic variant discussed
in the paper to the list of standardized PharmGKB association sentences that
assert an association about that variant.

KEYS -- list EVERY variant the paper studies or mentions (even ones only in a
table or in passing), in canonical form: rsIDs (e.g. "rs9923231") or star/HLA
alleles (e.g. "CYP2C19*2", "HLA-B*15:01"). For star-allele genes also include the
wild-type reference allele "<GENE>*1" as a key. A variant with no reported
association still appears as a key mapped to an empty list [].

VALUES -- follow PharmGKB conventions EXACTLY (this is how the gold is written):
  - ALLELE/DIPLOTYPE FRAMING for star-allele genes -- use star alleles and
    diplotypes (e.g. "CYP2D6 *3/*3 + *4/*4", "UGT1A1 *6 + *28"), NOT nucleotide
    genotypes ("AA"/"GA") and NOT metabolizer labels ("PM/IM", "poor
    metabolizer"); translate to the underlying alleles/diplotypes when the paper
    reports by genotype letters or metabolizer status.
  - FILE UNDER EVERY CONSTITUENT ALLELE. An association about a diplotype or an
    allele comparison is filed under EACH star allele it names AND under the
    comparison allele, including the "<GENE>*1" reference -- the identical
    sentence appears under each of those keys. (Only star/HLA-allele sentences are
    cross-filed this way; an rsID-genotype association is filed only under its
    rsID.)
  - COMBINE co-reported outcomes the way the paper groups them into ONE sentence
    (e.g. "Neutropenia, Leukopenia or Diarrhea"); do not split one finding into
    near-duplicates, and do not invent reciprocal restatements or genotype groups
    the paper never discusses.
  - Each sentence states the allele/diplotype, polarity ("is" / "is not
    associated"), direction ("increased"/"decreased") when applicable, the
    phenotype or clinical outcome (the paper's terms), the drug (when relevant),
    and the comparison group ("as compared to ...") when stated.

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
    kwargs = dict(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": markdown_content},
        ],
        response_format={"type": "json_object"},
    )
    # claude-opus-4-8 deprecates the `temperature` param; only send it where supported.
    if "opus-4-8" not in MODEL:
        kwargs["temperature"] = 0
    else:
        # Extended thinking: let opus reason through direction/polarity/comparison
        # framing before emitting the per-variant sentences (capture-quality axis).
        kwargs["reasoning_effort"] = "high"
    resp = litellm.completion(**kwargs)
    data = _extract_json_object(resp.choices[0].message.content or "")
    vs = data.get("variant_sentences", {})
    if not isinstance(vs, dict):
        vs = {}
    variant_sentences = {str(k): (v or []) for k, v in vs.items()}
    return {"variant_sentences": variant_sentences}
