# tools

A **shared, cross-run library of reusable tools** for annotation attempts. Unlike Karpathy's
single-file setup, this lets the agent accumulate capability over time instead of
re-deriving it in every attempt.

## What it is

- Plain, importable Python (`from src.tools.<module> import ...`) — a normal package, so
  (unlike timestamped attempt folders) attempts can import it directly.
- **Shared across attempts and runs**, and it persists across branches — so a helper one
  attempt builds is available to all future attempts.

## Rules

- **Attempts import from here freely.**
- **The model may add or extend tools here** (this is editable, unlike the fixed
  interface/scorer/harness) whenever logic is reusable beyond a single attempt.
- Keep each tool focused and documented: what it does, how to call it, what it depends on.
- Extract reusable logic out of an attempt and into a tool once a second attempt would
  benefit from it.

## Examples of useful tools

- variant normalization / rsID extraction (e.g. `regex_variants.py`)
- markdown section splitting / table extraction
- JSON repair / schema-coercion for model output
