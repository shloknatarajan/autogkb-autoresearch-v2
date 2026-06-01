"""
Edit ONLY this file (see program.md).

It must expose `predict(markdown_content) -> {"variants": [...], "sentences": [...]}`
for one paper. All model calls go through litellm so any provider works -- swap
models by changing `MODEL` (or the model string passed to litellm).

This is the BASELINE: a single litellm call asking for variants + standardized
sentences as JSON. The autoresearch loop hacks this file to raise sentence_f1.
"""

import json

import litellm

MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are a PharmGKB curator. You read the full text of a \
pharmacogenomics paper (in markdown) and extract its variant annotations.

Produce two things:

1. "variants": the list of genetic variant identifiers studied in the paper.
   Use canonical forms: rsIDs (e.g. "rs9923231") or star/HLA alleles
   (e.g. "CYP2C19*2", "HLA-B*15:01"). List each distinct variant once.

2. "sentences": a list of standardized association sentences, one per
   variant-drug-phenotype association the paper reports. Follow the PharmGKB
   standardized-sentence style exactly, e.g.:
     "CYP2C19 *1/*2 + *2/*2 is not associated with increased likelihood of Major
      Adverse Cardiac Events when treated with clopidogrel as compared to CYP2C19 *1/*1."
     "Genotype CT + TT is associated with decreased dose of warfarin in people with
      Atrial Fibrillation as compared to genotype CC."
   Each sentence states: the variant/genotype, polarity ("is" / "is not
   associated"), direction ("increased" / "decreased"), the phenotype or outcome,
   the drug (when relevant), and the comparison group/allele when stated.

Only include associations actually supported by the paper. Return JSON only:
{ "variants": ["..."], "sentences": ["..."] }"""


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
    return {
        "variants": data.get("variants", []),
        "sentences": data.get("sentences", []),
    }
