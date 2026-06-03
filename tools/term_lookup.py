"""Term normalization for PharmGKB/ClinPGx drugs and variants.

Thin wrapper around the ``clinpgx-term-lookup`` PyPI package
(https://pypi.org/project/clinpgx-term-lookup/) that gives attempts a single,
cached entry point for mapping free-text terms onto canonical ClinPGx records.

Each lookup returns a plain dict (or None on a miss) so it drops cleanly into
JSON output:

    {"id", "name", "url", "score", "source", "raw_input"}

- ``normalize_drug("tylenol")``      -> resolves brands via RxNorm to the
                                        PharmGKB ingredient (acetaminophen).
- ``normalize_variant("CYP2C9*2")``  -> routes rsIDs to the variant endpoint
                                        and star/HLA alleles to haplotypes.
- ``normalize_terms([...])``         -> batch helper; dedups and preserves order.

Lookups hit the network (PharmGKB + RxNorm), so results are cached per process
to keep repeated calls within an attempt cheap.

Usage (import):
    from tools.term_lookup import normalize_drug, normalize_variant

Usage (CLI smoke test):
    python tools/term_lookup.py drug warfarin tylenol
    python tools/term_lookup.py variant rs1799853 CYP2C9*2
"""

from functools import lru_cache
from typing import List, Optional

from clinpgx_term_lookup import DrugLookup, VariantLookup

_DRUGS = DrugLookup()
_VARIANTS = VariantLookup()


def _first(results) -> Optional[dict]:
    """Return the top result as a plain dict, or None if there were no matches."""
    if not results:
        return None
    return results[0].model_dump()


@lru_cache(maxsize=4096)
def normalize_drug(term: str, threshold: float = 0.8) -> Optional[dict]:
    """Resolve a drug/chemical name to its canonical ClinPGx record.

    Exact PharmGKB names match directly; brand names and misspellings fall back
    to RxNorm fuzzy matching, then re-query PharmGKB by the resolved ingredient.
    Returns the best match as a dict, or None if nothing resolved.
    """
    term = (term or "").strip()
    if not term:
        return None
    return _first(_DRUGS.search(term, threshold=threshold, top_k=1))


@lru_cache(maxsize=4096)
def normalize_variant(term: str, threshold: float = 0.8) -> Optional[dict]:
    """Resolve a variant to its canonical ClinPGx record.

    rsIDs (``rs...``) hit the variant endpoint; everything else is treated as a
    star/HLA allele and hits the haplotype endpoint. Returns the best match as a
    dict, or None on a miss.
    """
    term = (term or "").strip()
    if not term:
        return None
    return _first(_VARIANTS.search(term, threshold=threshold, top_k=1))


def normalize_terms(terms: List[str], kind: str = "variant") -> dict:
    """Batch-normalize ``terms`` of a single ``kind`` ("drug" or "variant").

    Dedups inputs (case-insensitive, order-preserving) and returns a dict
    mapping each original term -> its match dict (or None). Caching makes
    duplicate terms across calls effectively free.
    """
    if kind not in ("drug", "variant"):
        raise ValueError(f"kind must be 'drug' or 'variant', got {kind!r}")
    fn = normalize_drug if kind == "drug" else normalize_variant

    out: dict = {}
    seen = set()
    for term in terms:
        key = (term or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out[term] = fn(term)
    return out


if __name__ == "__main__":
    import json
    import sys

    kind = sys.argv[1] if len(sys.argv) > 1 else "drug"
    terms = sys.argv[2:] or ["warfarin"]
    fn = normalize_drug if kind == "drug" else normalize_variant
    for t in terms:
        print(f"{t!r:30} -> {json.dumps(fn(t))}")
