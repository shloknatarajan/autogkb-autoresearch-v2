# iter5 (69db5ac) — opus + rsID-genotype & HLA framing rules (dev-miss-driven)

**Hypothesis:** a dev-set miss diagnostic (diag_misses.py on an opus dev run)
showed the largest opus miss class is **rsID variants**, where gold frames by
nucleotide genotype ("Genotypes CT + TT is associated with decreased activity of
DPYD as compared to genotype CC", "Allele C ... as compared to allele T") — which
my prompt only taught for star alleles and even discouraged. Second class: HLA
SCAR alleles under-enumerated. Added explicit rsID/SNP genotype-framing rule (group
genotypes, singular "is associated", always state comparison genotype/allele) + an HLA-enumeration/outcome-combining rule.

**Changed vs champion (iter2):** two new VALUES bullets (rsID framing, HLA). Same
opus model, same everything else.

**Numbers (val, 16 papers):**
- meaning_capture: **0.529** (champion 0.536) — flat, within ±0.03 noise
- variant_coverage: 0.899 (0.888) — +0.011
- sentence_coverage: 0.473 (0.513) — −0.04

**Effect:** similar (no clear gain; micro slightly down).

**Lesson:** the dev-diagnosed rsID/HLA fixes did NOT move val macro — within noise,
micro slightly worse. Either opus already handled most of these on val, or the val
distribution differs from the dev misses, or the gains were offset by the added
rules nudging framing elsewhere. Net: prompt elaboration on opus is in the noise
band, same as on gpt-5.4 (iter1). **Keep champion = iter2** (simpler prompt, same
score → simplicity criterion). Reinforces: opus's ceiling here is model-bound, not
prompt-bound.
