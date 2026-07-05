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

Some laws (e.g. القانون المدني) encode issuance-decree articles as numeric ("مادة 2 – على وزير العدل…") rather than ordinal form. A post-processing step in `run()` finds the **last** (not first) occurrence of a main-type article 1 and reclassifies any earlier numeric articles as `article_type="issuance"`.

**Why last, not first:** some PDFs (e.g. EG_RENT_1969) contain an explanatory memo ("مذكرة إيضاحية") that re-narrates the whole law's articles 1..N *before* the real codified text starts. Anchoring on the *first* article 1 keeps this memo narrative misclassified as "main", creating duplicate-article validation errors. Anchoring on the *last* article-1 occurrence correctly treats everything before the true codified start (including such memos) as preamble/issuance. Verified safe against EG_CIVIL_CODE (only one occurrence, no regression).

## Cross-reference exclusion must cover memo/bill references too

`_PRIMARY_RE` / `_PAREN_DIGIT_RE` need a negative lookahead (`_NOT_LAW_REFERENCE`) rejecting markers followed by "من (هذا/ذلك) (القانون|المشروع)" — covers both cross-refs to law text ("من هذا القانون") AND references to the bill/draft in an explanatory memo ("من المشروع"). Both are generic Arabic legislative-drafting phrases, not law-specific.

**Residual gap (accepted, not fixed):** free-narrative citations like "فقد قضت المادة 35 بان يكون..." (Article 35 then ruled that...) have no trailing "من القانون/المشروع" marker and can't be distinguished from a real header by a general rule without overfitting to one document. Treat single-document occurrences of this kind as a data-quality note for human review, not a regex target.

## Issuance-ordinal regex must tolerate OCR paren spacing

`_ISSUANCE_RE` (matches "(المادة الاولى)" etc.) needs `\(\s*المادة...\s*\)`, not a bare `\(المادة`. Gemini OCR sometimes emits `( المادة الاولى )` with spaces inside the parens depending on source PDF — this silently zeroed out issuance-article detection for an entire law (EG_LABOR_2025) until fixed. `_PAREN_DIGIT_RE` already had this tolerance; `_ISSUANCE_RE` didn't — check both when adding new marker regexes.

## count_article_markers must reuse the splitter, not duplicate logic

`utils/arabic_text.py::count_article_markers` (used for Stage 1.5 confidence) must delegate to `pipeline.stage_2_split._collect_hits` (deferred import to avoid circular dependency) and dedup by article number, rather than maintaining its own separate regex. A separate naive regex there drifted out of sync with the splitter's cross-reference exclusions and inflated confidence-stage counts.
