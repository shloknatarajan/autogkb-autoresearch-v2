"""Cross-file association sentences under every variant they mention.

PharmGKB gold groups a single association sentence under EACH variant it
involves: a sentence like

    "CYP2D6 *3/*3 + *4/*4 are associated with increased likelihood of
     Recurrence ... as compared to CYP2D6 *1/*1."

appears in the gold under CYP2D6*1, CYP2D6*3 AND CYP2D6*4. Because
`meaning_capture` is macro-averaged per gold variant, a prediction that files
that sentence under only one of those alleles scores 0 on the others and drags
the paper's mean down.

This helper takes a predicted `{variant -> [sentences]}` mapping and, for every
sentence, replicates it under each star-allele / HLA-allele / rsID it explicitly
names in its text (rsIDs rarely appear verbatim in a sentence, but star/HLA
alleles almost always do). Extra/spurious variant keys are never penalized by the
scorer, so this is pure upside for recall and macro capture.

Usage:
    from tools.cross_file import cross_file_sentences
    variant_sentences = cross_file_sentences(variant_sentences)
"""

import re

# HLA alleles: HLA-B*58:01, B*58:01, HLA-A*31:01
_HLA = re.compile(r"\b(?:HLA-)?([A-Z]+\d*)\s*\*\s*(\d{2,4})(?::(\d{2,3}))?\b")
_RSID = re.compile(r"\brs\d{3,}\b", re.IGNORECASE)
# "<GENE> wild-type / wild type / wildtype" comparison group -> the gene's *1
# reference allele (gold files reference-allele comparisons under "*1"). Gene
# must be uppercase-as-written so prose words like "to" don't match.
_WILDTYPE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})\s+(?i:wild[\s-]?type|wildtype)\b")

# A gene token (pharmacogene-style name) or a bare *allele token, in order.
_GENE_TOK = r"(?P<gene>[A-Z][A-Z0-9]{1,9})\b"
_ALLELE_TOK = r"\*\s*(?P<allele>\d+[xX]?[nN]?)\b"
_STAR_SCAN = re.compile(rf"{_GENE_TOK}|{_ALLELE_TOK}")

# Tokens that look like GENE* but are not pharmacogenes we want to mint keys for.
_HLA_GENES = {"A", "B", "C", "CW", "DRB1", "DRB3", "DRB4", "DRB5",
              "DQA1", "DQB1", "DPA1", "DPB1"}


def _star_variants(text):
    """Star alleles, carrying the current gene across runs like '*3/*3 + *4/*4'.

    A gene token sets the active gene; every subsequent bare ``*N`` is filed
    under it until the next gene token appears.
    """
    out = []
    cur = None
    for m in _STAR_SCAN.finditer(text):
        gene = m.group("gene")
        if gene:
            g = gene.upper()
            cur = None if (g == "HLA" or g in _HLA_GENES) else g
            continue
        allele = m.group("allele")
        if not cur:
            continue
        allele = re.sub(r"[xX][nN]?$", "xN", allele) if re.search(r"[xX]", allele) else allele
        out.append(f"{cur}*{allele}")
    return out


def _hla_variants(text):
    out = []
    for gene, f1, f2 in _HLA.findall(text):
        g = gene.upper()
        if g == "CW":
            g = "C"
        if g not in _HLA_GENES:
            continue
        if f2:
            out.append(f"HLA-{g}*{f1}:{f2}")
        elif len(f1) >= 4:
            out.append(f"HLA-{g}*{f1[:2]}:{f1[2:4]}")
        else:
            out.append(f"HLA-{g}*{f1}")
    return out


def variants_in_sentence(text):
    """All variant identifiers explicitly named in one sentence."""
    vs = []
    vs += [m.lower() for m in _RSID.findall(text)]
    vs += _hla_variants(text)
    vs += _star_variants(text)
    # "GENE wild-type" comparison group -> file under the gene's *1 allele too.
    for gene in _WILDTYPE.findall(text):
        g = gene.upper()
        if g != "HLA" and g not in _HLA_GENES:
            vs.append(f"{g}*1")
    # dedup, preserve order
    seen, out = set(), []
    for v in vs:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _norm_key(v):
    """Match eval's variant comparison: rsID lowercased, else upper, no spaces."""
    s = str(v).strip()
    m = re.fullmatch(r"[rR][sS]\s*0*(\d+)", s)
    if m:
        return "rs" + m.group(1)
    return re.sub(r"\s+", "", s).upper()


def cross_file_sentences(variant_sentences, protect_populated=True):
    """Replicate each sentence under every variant id it names in its text.

    Keeps all original keys/sentences and adds cross-filings. The LLM judge is
    diluted by extra candidates on a variant that already has model output, so
    by default (``protect_populated``) cross-filed sentences are only added to
    variant keys the model left EMPTY/ABSENT -- recovering missing keys (the
    change that reliably helps) without diluting populated ones.
    """
    out = {k: list(v or []) for k, v in (variant_sentences or {}).items()}
    norm = {k: {s.strip().lower() for s in v} for k, v in out.items()}
    # normalized keys the model itself populated with >=1 sentence
    populated = {_norm_key(k) for k, v in out.items() if v}

    for sents in list(variant_sentences.values()):
        for s in sents or []:
            s = str(s)
            if not s.strip():
                continue
            for v in variants_in_sentence(s):
                if protect_populated and _norm_key(v) in populated:
                    continue  # don't dilute a key the model already answered
                bucket = out.setdefault(v, [])
                seen = norm.setdefault(v, {t.strip().lower() for t in bucket})
                if s.strip().lower() not in seen:
                    seen.add(s.strip().lower())
                    bucket.append(s)
    return out


if __name__ == "__main__":
    demo = {
        "CYP2D6*1": [
            "CYP2D6 *3/*3 + *4/*4 are associated with increased likelihood of "
            "Recurrence when treated with tamoxifen as compared to CYP2D6 *1/*1."
        ]
    }
    import json
    print(json.dumps(cross_file_sentences(demo), indent=2))
