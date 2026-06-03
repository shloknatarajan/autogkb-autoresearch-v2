"""Refresh the benchmark articles so they include supplementary materials.

Scope: only the PMCIDs that appear in the sentence benchmarks. For each one that
actually has supplementary material available (via the BioC API), refetch the
WHOLE paper with supplements and:
  1. overwrite base_data/articles/<pmcid>.md  (original backed up first)
  2. update the embedded `markdown_content` field in the sentence bench files

Idempotent and safe: a refetch is only accepted if it is non-empty, contains a
'## Supplementary Materials' section, and is not drastically shorter than the
existing article (guards against abstract-only fallbacks).

Usage:
    python tools/refresh_bench_supplements.py
"""

import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Unicode line-break / control characters that str.splitlines() treats as line
# boundaries but a plain "\n".split() does not. If any of these survive into a
# JSONL value, eval.py's `read_text().splitlines()` splits records mid-string
# and the whole bench fails to parse. Normalize them at the source.
_LINEBREAKS = re.compile("[\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")
# Other C0/C1 control chars (except tab \x09 and newline \x0a) that are illegal
# raw inside a strict-JSON string and add no content.
_CTRL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x9f]")


def sanitize_text(text):
    """Make fetched markdown safe to embed as one JSONL record.

    Converts every unicode line-break variant to a plain newline and strips
    other control characters, so each bench record stays a single
    splitlines()-line and parses under strict JSON.
    """
    if not text:
        return text
    text = _LINEBREAKS.sub("\n", text)
    text = _CTRL.sub("", text)
    return text


ARTICLES = os.path.join(ROOT, "base_data", "articles")
BACKUP = os.path.join(ROOT, "base_data", "articles_pre_supplement_backup")
BENCH = os.path.join(ROOT, "benchmarks")
SENTENCE_BENCHES = ["sentence_bench_by_variant.jsonl", "sentence_bench_collapsed.jsonl"]

EMAIL = os.environ.get("NCBI_EMAIL", "shlok.natarajan@stanford.edu")
os.environ.setdefault("NCBI_EMAIL", EMAIL)
RETRIES = 2
SLEEP = 0.5


def bench_pmcids():
    ids = set()
    for fn in SENTENCE_BENCHES:
        with open(os.path.join(BENCH, fn)) as fh:
            for line in fh:
                if line.strip():
                    ids.add(json.loads(line)["pmcid"])
    return sorted(ids)


def main():
    import pubmed_markdown as pm

    os.makedirs(BACKUP, exist_ok=True)
    client = pm.PubMedMarkdown(email=EMAIL)
    ids = bench_pmcids()
    print(f"benchmark pmcids: {len(ids)}", flush=True)

    refreshed = {}  # pmcid -> new markdown
    for i, pmcid in enumerate(ids, 1):
        # cheap check: does this paper have supplements at all?
        supp = None
        for attempt in range(RETRIES + 1):
            try:
                supp = pm.fetch_bioc_supplement(pmcid)
                break
            except Exception as e:  # noqa: BLE001
                if attempt < RETRIES:
                    time.sleep(1.0 + attempt)
                else:
                    print(
                        f"  [{i}] {pmcid}: detect ERR {type(e).__name__} {e}",
                        flush=True,
                    )
        time.sleep(SLEEP)
        if not supp:
            print(f"  [{i}] {pmcid}: no supplements", flush=True)
            continue

        # refetch whole paper with supplements
        new = None
        for attempt in range(RETRIES + 1):
            try:
                new = client.pmcid_to_markdown(pmcid, include_supplements=True)
                break
            except Exception as e:  # noqa: BLE001
                if attempt < RETRIES:
                    time.sleep(1.0 + attempt)
                else:
                    print(
                        f"  [{i}] {pmcid}: refetch ERR {type(e).__name__} {e}",
                        flush=True,
                    )
        time.sleep(SLEEP)

        # Strip embedded line-break/control chars so the markdown stays a single
        # JSONL record when written into the bench (see eval.py splitlines).
        new = sanitize_text(new)

        path = os.path.join(ARTICLES, pmcid + ".md")
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        reason = None
        if not new:
            reason = "empty fetch"
        elif "## Supplementary Materials" not in new:
            reason = "no supplement section in refetch"
        elif old and len(new) < 0.8 * len(old):
            reason = f"refetch too short ({len(new)} < 0.8*{len(old)})"
        if reason:
            print(f"  [{i}] {pmcid}: SKIP ({reason})", flush=True)
            continue

        if old:
            with open(os.path.join(BACKUP, pmcid + ".md"), "w", encoding="utf-8") as fh:
                fh.write(old)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        refreshed[pmcid] = new
        print(
            f"  [{i}] {pmcid}: refreshed ({len(old)} -> {len(new)} chars, "
            f"supp {len(supp)} chars)",
            flush=True,
        )

    print(f"\nrefreshed {len(refreshed)} articles", flush=True)

    # update embedded markdown_content in the sentence bench files
    for fn in SENTENCE_BENCHES:
        p = os.path.join(BENCH, fn)
        rows = [json.loads(l) for l in open(p) if l.strip()]
        changed = 0
        for r in rows:
            if r["pmcid"] in refreshed:
                r["markdown_content"] = refreshed[r["pmcid"]]
                changed += 1
        tmp = p + ".tmp"
        with open(tmp, "w") as fh:
            for r in rows:
                # ensure_ascii=True guarantees no raw line-break/control char
                # survives into a record, keeping the bench splitlines-safe.
                fh.write(json.dumps(r, ensure_ascii=True) + "\n")
        os.replace(tmp, p)
        print(f"  {fn}: updated markdown_content in {changed} rows", flush=True)


if __name__ == "__main__":
    main()
