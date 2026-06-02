"""
Regex-based variant extraction from markdown text.

Self-contained, pure-regex extractors for pharmacogenomic variants
(rsIDs, star alleles, HLA alleles) found in article markdown text.

Ported from the autogkb-monorepo variant_finding utilities, keeping only
the parts that operate directly on a text string (no external SNP database
or article-retrieval infrastructure required).
"""

import json
import re

# ============================================================================
# Constants
# ============================================================================

# Gene families with star allele nomenclature
PGX_GENES = [
    "CYP2D6",
    "CYP2C9",
    "CYP2C19",
    "CYP2B6",
    "CYP3A4",
    "CYP3A5",
    "CYP4F2",
    "CYP2A6",
    "CYP1A2",
    "UGT1A1",
    "UGT2B7",
    "UGT2B15",
    "NUDT15",
    "DPYD",
    "TPMT",
    "NAT1",
    "NAT2",
    "SLCO1B1",
    "SLCO1B3",
    "SLCO2B1",
    "ABCB1",
    "ABCG2",
    "VKORC1",
    "IFNL3",
    "IFNL4",
]


# ============================================================================
# Normalization helpers
# ============================================================================


def normalize_hla(variant: str) -> str:
    """Normalize HLA allele format to HLA-X*XX:XX format."""
    variant = variant.upper()

    # Already normalized
    if re.match(r"HLA-[A-Z]+\d*\*\d+:\d+", variant):
        return variant

    # Handle formats like B*5801 -> HLA-B*58:01
    match = re.match(r"(?:HLA-)?([A-Z]+\d*)\*(\d{2,})(\d{2})?", variant)
    if match:
        gene = match.group(1)
        field1 = match.group(2)
        field2 = match.group(3)

        if len(field1) == 4 and field2 is None:
            field1, field2 = field1[:2], field1[2:]
        elif len(field1) > 2 and field2 is None:
            field2 = field1[2:]
            field1 = field1[:2]

        if field2:
            return f"HLA-{gene}*{field1}:{field2}"
        else:
            return f"HLA-{gene}*{field1}"

    return variant


def normalize_star_allele(gene: str, allele_num: str) -> str:
    """Normalize star allele format."""
    gene = gene.upper()
    # Remove trailing x/X for copy number variants but keep xN format
    allele_num = re.sub(r"[xX×].*$", "", allele_num)
    return f"{gene}*{allele_num}"


# ============================================================================
# Extraction functions
# ============================================================================


def _rejoin_split_rsids(text: str) -> str:
    """Rejoin rsIDs split by spaces from PDF table cell parsing.

    BioC supplement text from PDFs can split rsIDs at table cell boundaries,
    e.g. "rs7692 58 rs28371 696" should be "rs769258 rs28371696".

    Only rejoins when the trailing digits are followed by another rsID or
    an uppercase word (column header), avoiding false positives in prose
    like "rs12345 was found in 3 patients".
    """
    return re.sub(
        r"(rs\d{4,})\s+(\d{1,4})(?=\s+(?:rs\d|[A-Z])|\s*$)",
        r"\1\2",
        text,
    )


def extract_rsids(text: str) -> list[str]:
    """Extract rsID variants from text."""
    text = _rejoin_split_rsids(text)
    pattern = r"\brs\d{4,}\b"
    matches = re.findall(pattern, text, re.IGNORECASE)
    return [m.lower() for m in set(matches)]


# Gene token used by both HLA detection and extraction.
_HLA_GENES = r"(?:A|B|C|Cw|DRB1|DRB3|DRB4|DRB5|DQA1|DQB1|DPA1|DPB1)"


def _hla_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) character spans covered by HLA allele mentions.

    Used to suppress standalone star-allele matches (e.g. the ``*58`` inside
    ``HLA-B*58:01``) that would otherwise be misattributed to a pharmacogene.
    """
    spans = []
    hla_patterns = [
        # HLA-B*58:01, HLA-B*5801, HLA-B*58
        r"\bHLA-[A-Z]+\d*\*\d{2,}(?::\d{2})?\b",
        # B*58:01, B*5801 (no HLA- prefix)
        rf"\b{_HLA_GENES}\*\d{{2,}}(?::\d{{2}})?\b",
        # Parenthetical: HLA-B*38:(01/02), B*39:(01/05/06/09)
        rf"(?:HLA-)?{_HLA_GENES}\*\d{{2}}:?\([/\d]+\)",
    ]
    for pattern in hla_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            spans.append((match.start(), match.end()))
    return spans


def extract_star_alleles(text: str) -> list[str]:
    """Extract star allele variants from text.

    Handles:
    - Standard format: CYP2C9*3, UGT1A1*28
    - Space format: CYP2D6 *4, NUDT15 *3
    - Copy number: CYP2D6*1xN, *2xN
    """
    variants = []

    gene_pattern = "|".join(PGX_GENES)

    # Pattern 1: GENE*NUMBER format (standard)
    pattern1 = rf"\b({gene_pattern})\*(\d+[xX×]?[nN]?)\b"
    matches = re.findall(pattern1, text, re.IGNORECASE)
    for gene, allele in matches:
        normalized = normalize_star_allele(gene, allele)
        variants.append(normalized)

    # Pattern 2: GENE *NUMBER format (space between gene and asterisk)
    pattern2 = rf"\b({gene_pattern})\s+\*(\d+[xX×]?[nN]?)\b"
    matches = re.findall(pattern2, text, re.IGNORECASE)
    for gene, allele in matches:
        normalized = normalize_star_allele(gene, allele)
        variants.append(normalized)

    # Pattern 3: Standalone star alleles (*3, *4, etc.) - need gene context
    standalone_pattern = r"\*(\d{1,2})\b"

    # Spans covered by HLA alleles (e.g. HLA-B*58:01). Standalone "*NN" hits
    # inside these are HLA fields, not pharmacogene star alleles, so skip them.
    hla_spans = _hla_spans(text)

    def _inside_hla(pos: int) -> bool:
        return any(start <= pos < end for start, end in hla_spans)

    # Find all gene mentions and their positions
    gene_mentions = []
    for gene in PGX_GENES:
        for match in re.finditer(rf"\b{gene}\b", text, re.IGNORECASE):
            gene_mentions.append((match.start(), match.end(), gene.upper()))

    # Pattern for diplotypes like *1xN/*2, *1/*10xN
    diplotype_pattern = r"\*(\d{1,2})[×xX]?[nN]?/\*(\d{1,2})[×xX]?[nN]?"
    for match in re.finditer(diplotype_pattern, text):
        allele1 = match.group(1)
        allele2 = match.group(2)
        diplotype_pos = match.start()

        if _inside_hla(diplotype_pos):
            continue

        # Find the nearest gene mention within 800 characters before
        nearest_gene = None
        min_distance = 800

        for gene_start, gene_end, gene_name in gene_mentions:
            if gene_end <= diplotype_pos:
                distance = diplotype_pos - gene_end
                if distance < min_distance:
                    min_distance = distance
                    nearest_gene = gene_name

        if nearest_gene:
            variants.append(f"{nearest_gene}*{allele1}")
            variants.append(f"{nearest_gene}*{allele2}")
            if "×" in match.group(0) or "x" in match.group(0).lower():
                variants.append(f"{nearest_gene}*{allele1}xN")
                variants.append(f"{nearest_gene}*{allele2}xN")

    # Find all standalone star alleles
    for match in re.finditer(standalone_pattern, text):
        allele_num = match.group(1)
        allele_pos = match.start()

        if _inside_hla(allele_pos):
            continue

        # Find the nearest gene mention within 200 characters before
        nearest_gene = None
        min_distance = 200

        for gene_start, gene_end, gene_name in gene_mentions:
            if gene_end <= allele_pos:
                distance = allele_pos - gene_end
                if distance < min_distance:
                    min_distance = distance
                    nearest_gene = gene_name

        if nearest_gene:
            normalized = normalize_star_allele(nearest_gene, allele_num)
            variants.append(normalized)

    # Pattern 4: Copy number variants with xN suffix
    xn_pattern = rf"\b({gene_pattern})\*(\d+)[xX×][nN]?\b"
    matches = re.findall(xn_pattern, text, re.IGNORECASE)
    for gene, allele in matches:
        normalized = normalize_star_allele(gene, allele)
        variants.append(normalized)
        variants.append(f"{gene.upper()}*{allele}xN")

    return list(set(variants))


def extract_hla_alleles(text: str) -> list[str]:
    """Extract HLA allele variants from text.

    Handles multiple formats:
    - HLA-B*58:01
    - HLA-B*5801
    - B*58:01
    - B*5801
    - HLA-B*38:(01/02) - parenthetical notation
    - B*39:(01/05/06/09)
    """
    variants = []

    hla_genes = _HLA_GENES

    # With HLA- prefix
    pattern1 = r"\bHLA-([A-Z]+\d*)\*(\d{2,}):?(\d{2})?\b"
    matches = re.findall(pattern1, text, re.IGNORECASE)
    for gene, f1, f2 in matches:
        if f2:
            variants.append(f"HLA-{gene.upper()}*{f1}:{f2}")
        elif len(f1) >= 4:
            variants.append(f"HLA-{gene.upper()}*{f1[:2]}:{f1[2:4]}")
        else:
            variants.append(f"HLA-{gene.upper()}*{f1}")

    # Without HLA- prefix
    pattern2 = rf"\b({hla_genes})\*(\d{{2,}})(?::(\d{{2}}))?\b"
    matches = re.findall(pattern2, text, re.IGNORECASE)
    for gene, f1, f2 in matches:
        gene = gene.upper()
        if gene == "CW":
            gene = "C"
        if f2:
            variants.append(f"HLA-{gene}*{f1}:{f2}")
        elif len(f1) >= 4:
            variants.append(f"HLA-{gene}*{f1[:2]}:{f1[2:4]}")
        else:
            variants.append(f"HLA-{gene}*{f1}")

    # Parenthetical notation: HLA-B*38:(01/02) or B*39:(01/05/06/09)
    paren_pattern = rf"(?:HLA-)?({hla_genes})\*(\d{{2}}):?\(([/\d]+)\)"
    matches = re.findall(paren_pattern, text, re.IGNORECASE)
    for gene, field1, alleles_str in matches:
        gene = gene.upper()
        if gene == "CW":
            gene = "C"
        allele_nums = alleles_str.split("/")
        for allele_num in allele_nums:
            if allele_num.isdigit():
                variants.append(f"HLA-{gene}*{field1}:{allele_num}")

    return list(set(variants))


def extract_all_variants(text: str) -> list[str]:
    """Extract all variant types (rsIDs, star alleles, HLA alleles) from text."""
    variants = []
    variants.extend(extract_rsids(text))
    variants.extend(extract_star_alleles(text))
    variants.extend(extract_hla_alleles(text))
    return list(set(variants))


def get_variant_types(variants: list[str]) -> dict:
    """Categorize variants by type into rsids, star_alleles, hla_alleles, other."""
    result: dict[str, list[str]] = {
        "rsids": [],
        "star_alleles": [],
        "hla_alleles": [],
        "other": [],
    }
    pgx_genes_upper = [g.upper() for g in PGX_GENES]
    for v in variants:
        v_upper = v.upper()
        if v_upper.startswith("RS") and v_upper[2:].isdigit():
            result["rsids"].append(v)
        elif v_upper.startswith("HLA-"):
            result["hla_alleles"].append(v)
        elif "*" in v and any(g in v_upper for g in pgx_genes_upper):
            result["star_alleles"].append(v)
        else:
            result["other"].append(v)
    return result


# ============================================================================
# LLM response parsing
# ============================================================================


def extract_json_array(text: str) -> list[str]:
    """
    Extract JSON array from LLM response.

    Handles various formats:
    - Pure JSON array: ["rs9923231", "CYP2C9*2"]
    - JSON in markdown code block: ```json\\n["rs9923231"]\\n```
    - JSON with explanation text before/after
    """
    # First try to extract from code blocks
    code_block_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if code_block_match:
        json_str = code_block_match.group(1)
    else:
        # Try to find JSON array anywhere in the text
        json_match = re.search(r"\[.*?\]", text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            return []

    try:
        result = json.loads(json_str)
        if isinstance(result, list):
            return [str(v).strip() for v in result]
        return []
    except json.JSONDecodeError:
        return []
