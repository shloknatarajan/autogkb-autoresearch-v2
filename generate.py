"""
Generation driver for the autogkb sentence-bench autoresearch loop.

Runs `annotation_pipeline.predict()` over the chosen split and writes one
prediction file per paper into a results folder:

    results/<name>/<pmcid>.json   ==  {"pmcid", "variants": [...], "sentences": [...]}

`eval.py` then scores a results folder by path. Generation (which costs
pipeline-model calls) is decoupled from scoring (which costs judge calls), so
you can re-score the same generations without regenerating.

Usage:
    uv run generate.py --out results/baseline               # generate on val
    uv run generate.py --out results/baseline --split dev
    uv run generate.py --out results/smoke --split dev --limit 2
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from eval import load_bench, split_bench

load_dotenv()


def generate(out_dir, split, limit):
    import annotation_pipeline

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    dev, val = split_bench(load_bench())
    chosen = {"dev": dev, "val": val, "all": dev + val}[split]
    if limit:
        chosen = chosen[:limit]

    for r in chosen:
        pred = annotation_pipeline.predict(r["markdown_content"])
        record = {
            "pmcid": r["pmcid"],
            "variants": pred.get("variants", []),
            "sentences": pred.get("sentences", []),
        }
        (out / f"{r['pmcid']}.json").write_text(json.dumps(record, indent=2))
        print(f"  {r['pmcid']}: {len(record['variants'])} variants, "
              f"{len(record['sentences'])} sentences")

    print(f"wrote {len(chosen)} generations to {out}/")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", required=True, help="results folder to write into, e.g. results/baseline")
    parser.add_argument("--split", choices=["val", "dev", "all"], default="val",
                        help="which subset to generate for (default: val)")
    parser.add_argument("--limit", type=int, default=None,
                        help="only generate the first N papers (quick smoke test)")
    args = parser.parse_args()
    generate(args.out, args.split, args.limit)


if __name__ == "__main__":
    main()
