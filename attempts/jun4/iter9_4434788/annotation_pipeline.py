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


# Second pass aimed only at RECALL: associations the first reader tends to miss.
# Same PharmGKB house style so the union stays consistent (no contradictory
# framings that would confuse the per-variant judge).
SECOND_PASS_PROMPT = (
    SYSTEM_PROMPT
    + """

SECOND-PASS FOCUS: a first reader already listed the obvious associations. Recover
the ones easy to MISS, in the SAME PharmGKB style and conventions above:
  - associations reported only in tables, figures, or supplementary text;
  - additional variants (rsIDs / star / HLA alleles) mentioned only in passing;
  - findings for additional genotype/diplotype groups the paper actually reports.
Do NOT restate associations with flipped direction, and do NOT invent groups the
paper never discusses. Only add real, paper-supported associations."""
)

# Most gold variants carry 1-3 sentences; the per-variant judge is diluted by long
# lists. Cap each variant's candidates so the union's recall gain isn't drowned.
CAP = 5


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
    vs = data.get("variant_sentences", {})
    if not isinstance(vs, dict):
        return {}
    return {str(k): (v or []) for k, v in vs.items()}


def _vkey(v):
    return str(v).strip().lower().replace(" ", "")


def predict(markdown_content):
    # Two-pass recall union: combine the per-variant sentence groups from a primary
    # pass and a miss-focused second pass, deduped per variant.
    g1 = _one_pass(SYSTEM_PROMPT, markdown_content)
    g2 = _one_pass(SECOND_PASS_PROMPT, markdown_content)

    variant_sentences = {}
    key_to_canonical = {}
    for group in (g1, g2):
        for variant, sents in group.items():
            vk = _vkey(variant)
            if not vk:
                continue
            canonical = key_to_canonical.setdefault(vk, variant)
            bucket = variant_sentences.setdefault(canonical, [])
            seen = {s.strip().lower() for s in bucket}
            for s in sents:
                s = str(s)
                if s.strip() and s.strip().lower() not in seen:
                    seen.add(s.strip().lower())
                    bucket.append(s)

    # Cap per variant to limit judge dilution (primary-pass sentences kept first).
    variant_sentences = {k: v[:CAP] for k, v in variant_sentences.items()}
    return {"variant_sentences": variant_sentences}
