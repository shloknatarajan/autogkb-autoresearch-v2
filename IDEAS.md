# Ideas for pipeline
- Prompt optimizers like GEPA, DSPy, etc.
- Inject regex content into LM calls like https://github.com/shloknatarajan/autogkb-monorepo/blob/main/packages/pipeline/pipeline/modules/variant_finding/utils.py
- Term normalization using https://github.com/shloknatarajan/ClinPGxTermNorm

## Pipeline Idea
- Make the new input text something like
```
{{ original markdown }}

Found variants using regex:

| regex variant | normalized variant |
| --- | --- |
| `{{ variant 1 }}` | `{{ normalized variant 1 }}` |
| `{{ variant 2 }}` | `{{ normalized variant 2 }}` |
...

```
Use the provided regex tool 