---
name: Nezam splitter regex backtracking fix
description: Dual negative lookahead required on _PRIMARY_RE in stage_2_split.py to prevent two distinct failure modes in Arabic legal PDF article detection.
---

## The rule

`_PRIMARY_RE` in `nezam-legal-corpus/pipeline/stage_2_split.py` must have TWO negative lookaheads after the digit group:

```python
_PRIMARY_RE = re.compile(
    rf"{_MD}(?:مادة|المادة)\s+(?:\d+|[٠-٩]+)(?![\d٠-٩])(?![ \t\n]*[،,])",
    re.MULTILINE,
)
```

## The two lookaheads

**`(?![\d٠-٩])`** — prevents regex backtracking into partial numbers.
When `(?:\d+)` matches "864" greedily and the comma lookahead fails (e.g. "864\n ،"), the engine backtracks to "86". After "86" the next char is "4" (a digit), not a comma — so the comma lookahead PASSES and article 86 is falsely created. Adding this lookahead makes the digit group effectively atomic: if a digit follows, the match is rejected entirely.

**`(?![ \t\n]*[،,])`** — rejects cross-references followed by Arabic/Latin comma.
PDF line-wrap can place "المادة" at the start of a line even when it's a mid-sentence cross-reference (e.g. "المادة 864\n ، فان لم تتحقق"). The comma test distinguishes references from headers.

## Why both are needed

The comma lookahead alone is insufficient: it triggers backtracking that the first lookahead must block. Applying only the comma check re-creates the partial-number bug via a different path.

## Post-processing for issuance detection

Some laws (e.g. القانون المدني) encode issuance-decree articles as numeric ("مادة 2 – على وزير العدل…") rather than ordinal form. A post-processing step in `run()` finds the first occurrence of article 1 and reclassifies any earlier numeric articles as `article_type="issuance"`.
