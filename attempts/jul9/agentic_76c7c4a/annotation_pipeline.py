"""
Edit ONLY this file (see program.md).

It must expose `predict(markdown_content) -> {"variant_sentences": {variant: [...]}}`
for one paper: a mapping from each variant to the standardized association
sentences asserting an association about that variant.

AGENTIC (jul9): instead of one single-shot model call, run a Claude Agent SDK
loop that can search the paper, pull a deterministic regex candidate list,
confirm terms against the ClinPGx vocabulary, and revise its own draft before
committing. The model's system prompt is the jun5 champion's rich PharmGKB
prompt (opus-4-8 + that prompt = 0.558 mean meaning_capture) plus a workflow
section. Tools live in `agent_tools.py`.

Deviation from program.md's litellm-only convention: this file calls Anthropic
via claude-agent-sdk. eval.py's judge is untouched and still runs on litellm.
"""

import asyncio
import json

import agent_tools
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    ThinkingConfigDisabled,
    ToolUseBlock,
    query,
)

MODEL = "claude-opus-4-8"
MAX_TURNS = 10

# Champion (jun5 iter2) prompt, unchanged through the "Example style" block.
SYSTEM_PROMPT = """You are a PharmGKB curator. You read the full text of a \
pharmacogenomics paper (in markdown) and extract its variant annotations.

Produce a mapping from each genetic variant discussed in the paper to the list
of standardized PharmGKB association sentences that assert an association about
that variant.

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

WORKFLOW -- you have tools. Use them in this order:

1. Read the paper text in the user message.
2. Call `extract_variants` to get a deterministic regex candidate list. Reconcile
   it against your reading: it finds variants buried in tables that are easy to
   miss, but it cannot tell which the paper actually studies, and it misses
   variants written in prose or by genotype letters. Add what it found and you
   missed; ignore what it found that the paper does not really discuss.
3. Call `search_paper` on any variant, drug, or outcome you are unsure about --
   especially to read data and supplementary tables closely before deciding a
   variant has no reported association.
4. Call `lookup_term` when a variant or drug name might be misspelled, might be a
   brand name, or might not be a real term. Use the canonical name it returns to
   fix SPELLING ONLY. Never put a PharmGKB accession ID (e.g. "PA166153554") in a
   key -- keys are always the variant as written above.
5. REVISE, DO NOT INFLATE. Before submitting, re-check each sentence you drafted
   against the paper. You may DELETE a sentence you cannot point to a passage
   for, and you may CORRECT a wrong direction, polarity, drug, phenotype, or
   comparison group. Do NOT add sentences to be thorough, do not restate an
   association from the reciprocal allele's point of view, and do not split one
   finding into near-duplicates. A tight, faithful list scores better than a long
   one: each variant is scored on how much of its gold meaning you recovered, and
   extra sentences obscure it.
6. Call `submit_annotations` exactly once with the final mapping. This is the
   only way to return your answer -- prose in your final message is discarded."""


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


def _options():
    return ClaudeAgentOptions(
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,  # plain str => replaces the claude_code preset
        mcp_servers={"pgkb": agent_tools.build_server()},
        tools=[],  # no built-ins: no filesystem, no bash, no reading the gold
        allowed_tools=agent_tools.ALLOWED_TOOLS,
        permission_mode="dontAsk",  # headless: deny anything unlisted, never prompt
        setting_sources=[],  # do not load this repo's CLAUDE.md / .claude/ or program.md
        max_turns=MAX_TURNS,
        # Match the champion, which ran opus-4-8 with no extended thinking.
        # jun5 iter4 (reasoning_effort=high) scored 0.458, -0.08 vs champion.
        thinking=ThinkingConfigDisabled(type="disabled"),
    )


async def _run_agent(markdown_content):
    agent_tools.reset(markdown_content)
    fallback_text = ""
    stats = {"turns": 0, "cost_usd": 0.0, "tool_calls": []}

    async for msg in query(prompt=markdown_content, options=_options()):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    stats["tool_calls"].append(block.name.replace("mcp__pgkb__", ""))
        elif isinstance(msg, ResultMessage):
            fallback_text = msg.result or ""
            stats["turns"] = msg.num_turns
            stats["cost_usd"] = msg.total_cost_usd or 0.0

    return agent_tools.take_submission(), fallback_text, stats


def predict(markdown_content):
    try:
        submitted, fallback_text, stats = asyncio.run(_run_agent(markdown_content))
    except Exception as e:
        print(f"    agent run failed: {type(e).__name__}: {e}")
        return {"variant_sentences": {}}

    if submitted is None:
        # Agent hit the turn cap or errored before calling submit_annotations.
        submitted = _extract_json_object(fallback_text).get("variant_sentences", {})
        print(
            f"    WARN: no submit_annotations call; fell back to parsing prose "
            f"({len(submitted)} variants recovered)"
        )

    if not isinstance(submitted, dict):
        submitted = {}

    calls = stats["tool_calls"]
    summary = ", ".join(f"{c}×{calls.count(c)}" for c in sorted(set(calls))) or "none"
    print(f"    turns={stats['turns']} cost=${stats['cost_usd']:.4f} tools: {summary}")

    return {"variant_sentences": {str(k): (v or []) for k, v in submitted.items()}}
