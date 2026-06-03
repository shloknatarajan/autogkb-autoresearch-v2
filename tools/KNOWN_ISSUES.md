# Known Issues

## `regex_variants.py` — slow star-allele extraction (correct, not optimized)

**Status:** working as designed; left as-is intentionally.

`extract_all_variants` is correct but slow on the full corpus: ~7m30s for all
3,066 articles in `base_data/articles/` (≈147 ms/file), with the largest
articles taking ~0.6–0.7s each.

**Cause:** `extract_star_alleles` does a linear nearest-gene scan for every
standalone `*N` match and every diplotype match — O(gene-mentions × star-matches)
per file. On articles with many gene mentions and many star tokens this dominates
runtime.

**Verification (full run, 2026-06-02):**
- 3,066 / 3,066 files processed, **0 errors / crashes**
- 31,772 variants extracted (23,213 rsIDs · 7,051 star · 1,508 HLA · 0 uncategorized)
- 404 files with no variants (articles with no rsID/star/HLA tokens)
- Dev-set gold coverage 88.9%, **0 true regex gaps** (all verbatim variants caught)

**If it ever needs to be faster:** pre-sort gene mentions by position and use
bisect to find the nearest preceding gene, instead of scanning all mentions per
star match. Not worth it for one-off pipeline use.
