"""Refetch full papers (with supplements) for articles whose supplementary
materials are missing from base_data/articles/.

The original corpus was generated without BioC supplement content. This script:

  detect   - for every PMCID, query the BioC supplement endpoint (cheap) and
             record which articles actually have supplementary material available.
  refetch  - for those articles, refetch the WHOLE paper via
             pmcid_to_markdown(include_supplements=True) and overwrite the file,
             backing up the original first.

Both phases are resumable: progress is kept in a manifest JSON, so re-running
skips work already done. Run `detect` first, then `refetch`.

Usage:
    python tools/fetch_supplements.py detect
    python tools/fetch_supplements.py refetch
    python tools/fetch_supplements.py status
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLES = os.path.join(ROOT, "base_data", "articles")
BACKUP = os.path.join(ROOT, "base_data", "articles_pre_supplement_backup")
MANIFEST = os.path.join(ROOT, "tools", "supplement_manifest.json")

EMAIL = os.environ.get("NCBI_EMAIL", "shlok.natarajan@stanford.edu")
os.environ.setdefault("NCBI_EMAIL", EMAIL)

# Be polite to NCBI: ~3 req/s without an API key.
DETECT_SLEEP = 0.34
REFETCH_SLEEP = 0.5
RETRIES = 2  # retry transient HTTP 500s


def pmcids():
    return sorted(f[:-3] for f in os.listdir(ARTICLES) if f.endswith(".md"))


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as fh:
            return json.load(fh)
    return {}


def save_manifest(m):
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(m, fh, indent=2, sort_keys=True)
    os.replace(tmp, MANIFEST)


def detect():
    import pubmed_markdown as pm

    m = load_manifest()
    ids = pmcids()
    todo = [p for p in ids if p not in m or m[p].get("supp_chars") is None]
    print(f"detect: {len(ids)} total, {len(todo)} to query", flush=True)
    for i, pmcid in enumerate(todo, 1):
        chars = None
        for attempt in range(RETRIES + 1):
            try:
                s = pm.fetch_bioc_supplement(pmcid)
                chars = len(s) if s else 0
                break
            except Exception as e:  # noqa: BLE001
                chars = None
                if attempt < RETRIES:
                    time.sleep(1.0 + attempt)
                else:
                    print(f"  ERR {pmcid}: {type(e).__name__} {e}", flush=True)
        m.setdefault(pmcid, {})["supp_chars"] = chars
        if i % 50 == 0:
            save_manifest(m)
            have = sum(1 for v in m.values() if v.get("supp_chars"))
            print(
                f"  [{i}/{len(todo)}] queried; {have} with supplements so far",
                flush=True,
            )
        time.sleep(DETECT_SLEEP)
    save_manifest(m)
    have = sorted(p for p, v in m.items() if v.get("supp_chars"))
    print(f"detect done: {len(have)}/{len(ids)} articles have supplements", flush=True)


def refetch():
    import pubmed_markdown as pm

    os.makedirs(BACKUP, exist_ok=True)
    m = load_manifest()
    client = pm.PubMedMarkdown(email=EMAIL)
    targets = sorted(
        p for p, v in m.items() if v.get("supp_chars") and not v.get("refetched")
    )
    print(f"refetch: {len(targets)} articles to refetch", flush=True)
    ok = skipped = failed = 0
    for i, pmcid in enumerate(targets, 1):
        path = os.path.join(ARTICLES, pmcid + ".md")
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        new = None
        for attempt in range(RETRIES + 1):
            try:
                new = client.pmcid_to_markdown(pmcid, include_supplements=True)
                break
            except Exception as e:  # noqa: BLE001
                if attempt < RETRIES:
                    time.sleep(1.0 + attempt)
                else:
                    print(f"  ERR {pmcid}: {type(e).__name__} {e}", flush=True)

        # Safety guards: don't replace a full paper with a degraded/empty fetch.
        reason = None
        if not new:
            reason = "empty fetch"
        elif "## Supplementary Materials" not in new:
            reason = "no supplement section in refetch"
        elif old and len(new) < 0.8 * len(old):
            reason = f"refetch too short ({len(new)} < 0.8*{len(old)})"

        if reason:
            m[pmcid]["refetch_skipped"] = reason
            skipped += 1
            print(f"  SKIP {pmcid}: {reason}", flush=True)
        else:
            if old:
                with open(
                    os.path.join(BACKUP, pmcid + ".md"), "w", encoding="utf-8"
                ) as fh:
                    fh.write(old)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            m[pmcid]["refetched"] = True
            m[pmcid]["old_len"] = len(old)
            m[pmcid]["new_len"] = len(new)
            ok += 1
        if i % 20 == 0:
            save_manifest(m)
            print(f"  [{i}/{len(targets)}] ok={ok} skip={skipped}", flush=True)
        time.sleep(REFETCH_SLEEP)
    save_manifest(m)
    print(
        f"refetch done: {ok} overwritten, {skipped} skipped, backups in {BACKUP}",
        flush=True,
    )


def status():
    m = load_manifest()
    ids = pmcids()
    queried = sum(1 for v in m.values() if v.get("supp_chars") is not None)
    have = sum(1 for v in m.values() if v.get("supp_chars"))
    refetched = sum(1 for v in m.values() if v.get("refetched"))
    skipped = sum(1 for v in m.values() if v.get("refetch_skipped"))
    print(f"articles:        {len(ids)}")
    print(f"detect queried:  {queried}")
    print(f"have supplement: {have}")
    print(f"refetched:       {refetched}")
    print(f"refetch skipped: {skipped}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"detect": detect, "refetch": refetch, "status": status}[cmd]()
