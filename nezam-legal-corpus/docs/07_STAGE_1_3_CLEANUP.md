# Stage 1.3 — Arabic Text Cleanup

**Status:** ✅ Complete — pilot-tested on EG_PDPL and EG_ESIGN  
**File:** `pipeline/stage_1_3_cleanup.py`

---

## Purpose

Normalise the raw extracted text before confidence scoring and article splitting.
Every transform is tracked in a JSON audit log — no silent changes.

**Position in pipeline:**

```
Stage 1 (Extraction)
   → data/extracted_raw/{LAW_ID}.txt
Stage 1.3 (Cleanup)           ← this stage
   → data/extracted_clean/{LAW_ID}.txt
   → data/cleanup_audit_logs/{LAW_ID}_cleanup_audit.json
Stage 1.5 (Confidence Scoring)  reads from extracted_clean/
```

---

## Transforms Applied (in order)

| # | Transform | Target | Why |
|---|-----------|--------|-----|
| 1 | **NFC normalisation** | Unicode code-point composition | Unifies visually identical characters that have different byte representations |
| 2 | **Remove tatweel** | U+0640 (Arabic extension) | Decorative stretching not present in law text; masaar.net TXT files use it heavily |
| 3 | **Remove diacritics** | U+064B–U+065F, U+0670 | Harakat / shadda are absent from formal legislation; removal simplifies pattern matching |
| 4 | **Normalise Hamza** | أإآٱ → ا | Standard Arabic NLP normalisation; `أ` and `ا` are interchangeable in legal text |
| 5 | **Normalise Yeh** | ى ئ → ي | Alef maqsura (`ى`) and yeh with hamza (`ئ`) treated as equivalent |
| 6 | **Remove control chars** | U+0000–U+0008, U+000B, U+000C, U+000E–U+001F, U+007F | OCR artifacts, zero-width characters |
| 7 | **Collapse whitespace** | Multiple spaces → single space | Normalises OCR spacing irregularities |
| 8 | **Collapse newlines** | 3+ consecutive newlines → 2 | Preserves paragraph boundaries without excessive blank lines |

**What is NOT changed:**
- Arabic digits (٠١٢ etc.) — kept as-is, article numbers use them
- Punctuation (، ؛ ؟ . () —) — preserved for sentence-boundary detection in Stage 3.7
- Latin characters and digits — preserved (law numbers, article numbers in some sources)
- Paragraph boundaries (`\n\n`) — preserved, Stage 2 and Stage 3.7 depend on them

---

## Inputs

| Path | Description |
|------|-------------|
| `data/extracted_raw/{LAW_ID}.txt` | Raw text from Stage 1 |

Stage 1.3 never modifies the raw file — output always goes to `extracted_clean/`.

---

## Outputs

| File | Description |
|------|-------------|
| `data/extracted_clean/{LAW_ID}.txt` | Cleaned text — input for all subsequent stages |
| `data/cleanup_audit_logs/{LAW_ID}_cleanup_audit.json` | Character-level change counts |

### `_cleanup_audit.json` Schema

```json
{
  "law_id": "EG_PDPL",
  "extraction_source": "plaintext",
  "chars_before": 37003,
  "chars_after": 36283,
  "chars_removed": 720,
  "nfc_changed": 0,
  "tatweel_removed": 621,
  "diacritics_removed": 99,
  "hamza_normalised": 969,
  "yeh_normalised": 272,
  "control_removed": 0,
  "spaces_collapsed": 0,
  "newlines_collapsed": 0,
  "cleaned_at": "2026-06-21T14:49:09.663974+00:00"
}
```

Note: `hamza_normalised` and `yeh_normalised` count character *substitutions* (not removals), so they do not contribute to `chars_removed`.

---

## Pilot Results

| Law | Source | Before | After | Removed | Key changes |
|-----|--------|--------|-------|---------|------------|
| EG_PDPL | plaintext | 37,003 | 36,283 | 720 (1.95%) | 621 tatweel + 99 diacritics |
| EG_ESIGN | gemini_ocr | 14,065 | 14,052 | 13 (0.09%) | 9 tatweel + 4 diacritics |

**EG_PDPL:** masaar.net TXT source uses tatweel heavily as a formatting device (621 chars). Gemini OCR sources (EG_ESIGN) have almost no tatweel — Gemini strips it during generation.

---

## Effect on Confidence Scores

Confidence scoring (Stage 1.5) runs on the **cleaned** text. After cleanup, AMD can shift slightly because Hamza/Yeh normalisation changes the character sequences the article-marker regexes match against.

| Law | Confidence (raw) | Confidence (clean) | Change |
|-----|-----------------|-------------------|--------|
| EG_PDPL | 0.9528 | **0.9206** | −0.0322 |
| EG_ESIGN | 0.9288 | **0.9142** | −0.0146 |

Both still well above the 0.85 threshold. The small drop in AMD is acceptable — Stage 2 (article splitting) works on the normalised text and will correctly identify all article boundaries regardless.

---

## Stage 1.5 Fallback

Stage 1.5's `run()` function tries `extracted_clean/` first, then falls back to `extracted_raw/` if cleanup hasn't run. This allows Stage 1.5 to be run standalone without Stage 1.3 (e.g. for debugging).
