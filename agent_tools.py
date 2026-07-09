"""In-process Claude Agent SDK tools for the annotation pipeline.

Imported by `annotation_pipeline.py`. Four tools, no filesystem access:

  search_paper       re-read the paper text (tables, supplements) by regex
  extract_variants   deterministic rsID/star/HLA candidate list
  lookup_term        confirm a variant/drug is a real ClinPGx term
  submit_annotations the output channel -- the agent's final answer

The paper text and the submitted answer are module globals rather than
arguments/returns because (a) `@tool` registers module-level functions, and
(b) the Python `@tool` decorator forwards only `content`/`is_error` back to
the model, so a tool cannot return structured data to the caller. This is safe
because `generate.py` calls `predict()` strictly serially, one paper at a time.
`reset(markdown)` must be called before each agent run.
"""

import asyncio
import re

from claude_agent_sdk import create_sdk_mcp_server, tool

from tools.regex_variants import extract_all_variants
from tools.term_lookup import normalize_drug, normalize_variant

_CURRENT_PAPER: str = ""
_SUBMITTED: dict | None = None

# Bounded so a greedy regex can't blow up the agent's context window.
_MAX_MATCHES = 40
_CONTEXT_CHARS = 200


def reset(markdown_content: str) -> None:
    """Load a new paper and clear any previous submission. Call before each run."""
    global _CURRENT_PAPER, _SUBMITTED
    _CURRENT_PAPER = markdown_content
    _SUBMITTED = None


def take_submission() -> dict | None:
    """Return what the agent submitted for the current paper, or None if it never did."""
    return _SUBMITTED


def _text(s: str, is_error: bool = False) -> dict:
    out: dict = {"content": [{"type": "text", "text": s}]}
    if is_error:
        out["is_error"] = True
    return out


@tool(
    "search_paper",
    "Search the full text of the paper with a regular expression and get back the "
    "matching lines with surrounding context. Call this whenever you need to check "
    "what the paper actually says about a variant, drug, or outcome -- especially to "
    "read data tables and supplementary tables closely, where variants are often "
    "reported only once and are easy to miss on a first read.",
    {"pattern": str},
)
async def search_paper(args) -> dict:
    pattern = args["pattern"]
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return _text(f"Invalid regex {pattern!r}: {e}", is_error=True)

    hits = []
    for m in rx.finditer(_CURRENT_PAPER):
        start = max(0, m.start() - _CONTEXT_CHARS)
        end = min(len(_CURRENT_PAPER), m.end() + _CONTEXT_CHARS)
        hits.append(_CURRENT_PAPER[start:end].replace("\n", " ").strip())
        if len(hits) >= _MAX_MATCHES:
            break

    if not hits:
        return _text(f"No matches for {pattern!r}.")

    body = "\n\n---\n".join(f"[{i + 1}] ...{h}..." for i, h in enumerate(hits))
    capped = f" (capped at {_MAX_MATCHES})" if len(hits) >= _MAX_MATCHES else ""
    return _text(f"{len(hits)} match(es){capped} for {pattern!r}:\n\n{body}")


@tool(
    "extract_variants",
    "Run a deterministic regex extractor over the paper and return every rsID, star "
    "allele, and HLA allele it finds. Call this once early, then reconcile the list "
    "against your own reading: it will surface variants buried in tables that are easy "
    "to skip, but it has no judgement -- it cannot tell which variants the paper "
    "actually studies, and it misses variants written in prose or by genotype letters.",
    {},
)
async def extract_variants(args) -> dict:
    found = sorted(extract_all_variants(_CURRENT_PAPER))
    if not found:
        return _text("The regex extractor found no variants. Rely on your own reading.")
    return _text(
        f"Regex extractor found {len(found)} candidate variant(s):\n"
        + "\n".join(f"  {v}" for v in found)
        + "\n\nThese are candidates, not answers. Verify each against the paper."
    )


@tool(
    "lookup_term",
    "Confirm that a variant or drug name is a real term in the ClinPGx/PharmGKB "
    "controlled vocabulary, and get its canonical spelling. Call this when you are "
    "unsure whether a term you extracted is real, or when the paper uses a brand name "
    "(e.g. 'Plavix') or a nonstandard spelling and you need the generic/canonical form. "
    "Use the returned name ONLY to fix spelling in your sentences -- never change a "
    "variant key to a PharmGKB accession ID.",
    {"term": str, "kind": str},
)
async def lookup_term(args) -> dict:
    term = (args.get("term") or "").strip()
    kind = (args.get("kind") or "").strip().lower()
    if kind not in ("drug", "variant"):
        return _text(f"kind must be 'drug' or 'variant', got {kind!r}.", is_error=True)
    if not term:
        return _text("term must be a non-empty string.", is_error=True)

    fn = normalize_drug if kind == "drug" else normalize_variant
    try:
        # Sync + network-bound; keep it off the event loop.
        hit = await asyncio.to_thread(fn, term)
    except Exception as e:  # network flake shouldn't kill the run
        return _text(
            f"Lookup of {term!r} failed: {type(e).__name__}: {e}", is_error=True
        )

    if not hit:
        return _text(
            f"No ClinPGx {kind} matches {term!r}. It may be misspelled, may be a term "
            f"the vocabulary does not cover, or may not be a real {kind}."
        )
    # Deliberately omit hit["id"] -- a PharmGKB accession must never reach the output.
    return _text(
        f"{term!r} resolves to the canonical {kind} name {hit['name']!r} "
        f"(match score {hit.get('score')}, source {hit.get('source')}). "
        f"Use {hit['name']!r} as the spelling in sentences."
    )


@tool(
    "submit_annotations",
    "Record your final variant-to-sentences mapping. Call this exactly once, after you "
    "have finished reading and verifying. Keys must be variant identifiers exactly as "
    "written in the paper's canonical form -- rsIDs like 'rs9923231', star alleles like "
    "'CYP2C19*2', HLA alleles like 'HLA-B*15:01', and the wild-type '<GENE>*1'. NEVER "
    "use a PharmGKB accession ID (e.g. 'PA166153554') as a key. A variant with no "
    "reported association is still a key, mapped to an empty list.",
    {
        "type": "object",
        "properties": {
            "variant_sentences": {
                "type": "object",
                "description": "Map of variant identifier -> list of standardized "
                "PharmGKB association sentences about that variant.",
                "additionalProperties": {"type": "array", "items": {"type": "string"}},
            }
        },
        "required": ["variant_sentences"],
    },
)
async def submit_annotations(args) -> dict:
    global _SUBMITTED
    vs = args.get("variant_sentences")
    if not isinstance(vs, dict):
        return _text("variant_sentences must be an object.", is_error=True)

    leaked = [k for k in vs if re.fullmatch(r"PA\d{5,}", str(k).strip())]
    if leaked:
        return _text(
            f"Rejected: {leaked} are PharmGKB accession IDs, not variant identifiers. "
            "Re-key those entries using the variant as written in the paper "
            "(e.g. 'rs9923231', 'CYP2C19*2') and submit again.",
            is_error=True,
        )

    _SUBMITTED = {str(k): (v or []) for k, v in vs.items()}
    n_sent = sum(len(v) for v in _SUBMITTED.values())
    return _text(f"Recorded {len(_SUBMITTED)} variants, {n_sent} sentences.")


def build_server():
    """The in-process MCP server exposing the four tools as mcp__pgkb__<name>."""
    return create_sdk_mcp_server(
        name="pgkb",
        version="1.0.0",
        tools=[search_paper, extract_variants, lookup_term, submit_annotations],
    )


ALLOWED_TOOLS = [
    "mcp__pgkb__search_paper",
    "mcp__pgkb__extract_variants",
    "mcp__pgkb__lookup_term",
    "mcp__pgkb__submit_annotations",
]
