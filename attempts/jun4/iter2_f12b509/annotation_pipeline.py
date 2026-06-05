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

KEYS -- be EXHAUSTIVE about variant identifiers. List EVERY genetic variant the
paper studies or mentions, even ones appearing only in a table or in passing, in
canonical form -- rsIDs (e.g. "rs9923231") or star/HLA alleles (e.g. "CYP2C19*2",
"HLA-B*15:01"). Missing a variant key is the costly error; an extra plausible key
is harmless. A variant with no reported association still appears as a key mapped
to an empty list [].

VALUES -- be FAITHFUL and PRECISE, not verbose. For each variant, list ONLY the
association sentences the paper actually asserts about it. Do NOT invent
reciprocal restatements, do NOT enumerate genotype groups the paper never
discusses, and do NOT pad with speculative framings. One clean sentence per
distinct association the paper reports is ideal. Each sentence states the
variant/genotype, polarity ("is" / "is not associated"), direction ("increased" /
"decreased"), the phenotype or outcome, the drug (when relevant), and the
comparison group when stated. Example style:
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
