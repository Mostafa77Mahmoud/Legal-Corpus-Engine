# Diagnostic Session — Stage 1.5 / Stage 2.5 Failures (EG_RENT_1969, EG_LABOR_2025)

**Date:** 2026-07-05
**Scope:** Diagnose why Stage 1.5 confidence FAILED for EG_RENT_1969 and Stage 2.5
validation FAILED for EG_LABOR_2025. Only general (codebase-wide) bugs were fixed;
no law-specific hacks, no manual output edits, no schema/prompt/architecture changes.

## General bugs found and fixed (apply to all laws)

1. **`utils/arabic_text.py::count_article_markers`** used a naive, unanchored regex
   with no cross-reference exclusion or number-based dedup — inconsistent with the
   splitter's own (correct) logic, causing inflated marker counts that fed Stage 1.5
   confidence. Fixed by delegating to `pipeline.stage_2_split._collect_hits` and
   dedup-by-article-number.
2. **`pipeline/stage_2_split.py` — `_PRIMARY_RE` / `_PAREN_DIGIT_RE`** lacked a
   cross-reference exclusion for phrases like "المادة 604 من القانون المدني" or
   "المادة 143 من هذا القانون" — these were being counted as real article headers.
   Added a shared negative-lookahead `_NOT_LAW_REFERENCE` excluding "من (هذا/ذلك)
   (القانون|المشروع)" (extended to cover explanatory-memo "bill" references too).
3. **`pipeline/stage_2_split.py` — preamble reclassification anchor** used the
   *first* occurrence of article 1 to decide what counts as "issuance" preamble vs.
   codified main text. Some laws (e.g. EG_RENT_1969) have an explanatory memo
   ("مذكرة إيضاحية") that re-narrates articles 1..N *before* the real codified law
   starts, which duplicated article numbering. Changed the anchor to the *last*
   occurrence of article 1 with type "main" (verified no regression on
   EG_CIVIL_CODE, which only has one occurrence).
4. **`pipeline/stage_2_split.py` — `_ISSUANCE_RE`** required a literal `(المادة`
   with no space, but Gemini OCR sometimes emits `( المادة الاولى )` with spaces
   inside the parentheses. This caused EG_LABOR_2025's 10 ordinal issuance articles
   to be silently missed (issuance_count=0), producing a large "orphan preamble"
   warning. Fixed by allowing optional whitespace after `(` and before `)`,
   matching the pattern already used by `_PAREN_DIGIT_RE`.

Registry `expected_article_count` was also corrected for both laws (a pre-existing
"تقدير أولي" / preliminary-estimate field, updated per its own documented intent,
not a hack): EG_RENT_1969 36→48, EG_LABOR_2025 315→298 (confirmed true document end
via the signing block).

## Result after fixes

- **EG_LABOR_2025**: full pipeline reran clean, Stage 1→7 all PASS (0 errors
  throughout). 308 articles → 321 chunks released to `data/releases/EG_LABOR_2025`.
- **EG_RENT_1969**: Stage 1.5 confidence now passes (0.9516). Stage 2.5 duplicate
  errors dropped from 48 → 1. The one remaining error is a genuine **data-quality
  issue, not a code bug** (see below) — it is left unfixed pending human review, per
  instructions not to write law-specific hacks.

## Residual issue — human review needed (EG_RENT_1969 only)

Article 35 is mentioned a **third time** inside the explanatory memo ("مذكرة
إيضاحية") using the free-narrative phrasing:

> "...فقد قضت المادة 35 بان يكون لهؤلاء الملاك الذين يقومون باعمال الترميم..."
> ("...Article 35 then ruled/stipulated that these owners who carry out
> restoration work shall have...")

This is a genuine reference to Article 35, but it is **not** structurally
distinguishable from a real article header via a general rule: it lacks the
"من هذا القانون" / "من المشروع" trailing citation phrase that the other ~47
duplicate false-positives had, and it happens to fall at the start of a line
(due to PDF text wrapping), so the line-start-anchored marker regex matches it.

A further fix would require detecting free-form narrative verbs ("قضت", "نصت")
immediately preceding "المادة N" as a citation pattern. This was deliberately
**not implemented** — it was observed in only one document and generalizing from
a single example risks becoming an overfit, law-specific hack disguised as a
"general" rule, which is out of scope for this diagnostic pass.

Downstream symptom: because this false "article 35" match sits right after the
document's real final article (48), everything from the true end of Article 48 up
to this false marker gets swallowed into Article 48's body, producing two
`E005 OVERSIZED_ARTICLE` warnings (48: ~2800 words, 35: ~650-720 words vs.
median 68) and a `W001 OVER_COUNT` warning (98 found vs. 48 expected). These are
symptoms of the same root cause, not separate issues.

**Recommendation:** A human reviewer should inspect
`data/split_articles/EG_RENT_1969/articles.json` (`EG_RENT_1969_098`, the
duplicate "Article 35" entry) and decide whether to manually exclude that specific
memo-narrative sentence from the source PDF text, or accept the current split with
this one flagged article. No pipeline code should be changed further for this
single, non-generalizable case. EG_RENT_1969 has **not** been advanced past Stage
2.5 and has not been released, pending this decision.
