# Stage 1.5 — Extraction Confidence Scoring

**Status:** ✅ Complete — pilot-tested on EG_PDPL and EG_ESIGN  
**File:** `pipeline/stage_1_5_val_extract.py`

---

## Purpose

Quality gate on the raw extracted text. Calculates a 0–1 confidence score using 5 factors derived from the law's expected structure. If `confidence < 0.85`, the law is flagged for human review.

This stage runs immediately after Stage 1 in the current pilot runner (`run_pilot.py`). In the full pipeline it will run after Stage 1.3 (cleanup).

---

## 5-Factor Confidence Formula

```
Confidence = (AMD_norm × 0.25) + (ACD_norm × 0.25) + (EACC_norm × 0.25)
           + (SHC_norm  × 0.15) + (CS_norm  × 0.10)
```

| Factor | Name | Weight | What It Measures |
|--------|------|--------|-----------------|
| **ACD** | Arabic Character Density | 0.25 | Fraction of text that is Arabic characters — low ACD = garbled encoding |
| **AMD** | Article Marker Density | 0.25 | `found_markers / expected_articles` — proximity to 1.0 = correct count |
| **EACC** | Expected Article Count Coverage | 0.25 | `min(1, found/expected)` — penalizes missing articles |
| **SHC** | Structural Heading Coverage | 0.15 | `min(1, found_headings/expected_headings)` — detects missing chapters |
| **CS** | Corruption Score (inverted) | 0.10 | Replacement character density (U+FFFD) — high = OCR failure |

### Normalization Rules

| Factor | Raw Value | Normalization |
|--------|-----------|---------------|
| ACD | 0–1 | Used as-is |
| AMD | markers/expected | `max(0, 1 - abs(1 - amd))` — penalizes both over and under-count |
| EACC | min(1, markers/expected) | Used as-is |
| SHC | min(1, headings/expected) | Used as-is; if expected=0, SHC=1.0 (not applicable) |
| CS | replacement_char_density | `max(0, 1 - (cs / 0.05))` — 5% replacement chars = score 0 |

---

## Threshold

| Score | Result |
|-------|--------|
| ≥ 0.85 | **PASS** — proceed to next stage |
| < 0.85 | **FAIL** — flagged for human review, pipeline halts |

---

## Article Marker Patterns Supported

The `count_article_markers()` function in `utils/arabic_text.py` handles all known Egyptian law formats:

| Pattern | Example | Source |
|---------|---------|--------|
| Standard | `مادة 5` / `المادة 5` | Most PDFs |
| Abbreviated | `ما5` / `ما 6` | Garbled/encoding-broken PDFs |
| Paren-digit | `مادة (١)` / `مادة ( ٢ )` | masaar.net TXT, some PDFs |
| Ordinal-paren | `(المادة الأولى)` ... `(المادة السابعة)` | Issuance articles |

---

## Outputs

| File | Description |
|------|-------------|
| `data/extracted_raw/{LAW_ID}_confidence.json` | Full confidence report |

### `_confidence.json` Schema

```json
{
  "law_id": "EG_PDPL",
  "extraction_source": "plaintext",
  "char_count": 37003,
  "article_markers_found": 56,
  "expected_article_count": 56,
  "structural_headings_found": 24,
  "expected_chapter_headings": 14,
  "acd": 0.8114,  "acd_norm": 0.8114,
  "amd": 1.0,     "amd_norm": 1.0,
  "eacc": 1.0,    "eacc_norm": 1.0,
  "shc": 1.0,     "shc_norm": 1.0,
  "cs": 0.0,      "cs_norm": 1.0,
  "confidence_score": 0.9528,
  "threshold": 0.85,
  "passed": true,
  "manual_review": false,
  "scored_at": "2026-06-21T13:13:28.267333Z"
}
```

---

## Pilot Results

| Law | Source | Confidence | Pass? | Notes |
|-----|--------|-----------|-------|-------|
| EG_PDPL | plaintext | **0.9528** | ✅ | 56/56 articles, 24 headings found |
| EG_ESIGN | gemini_ocr | **0.9288** | ✅ | 35 markers found (expected 32) — slight over-count from OCR |

**EG_ESIGN note:** AMD = 1.0938 (35/32 = 9% over-count). AMD_norm = 0.9062. The extra 3 markers are likely section headers that the OCR formatted as article markers. Acceptable at Stage 1.5; the article splitter (Stage 2) will resolve them.

---

## Dependency on law_registry

Each law's expected counts are registered in `config/law_registry.py`:

```python
LawEntry(
    law_id="EG_PDPL",
    expected_article_count=56,     # 7 issuance + 49 main law articles
    expected_chapter_headings=14,  # الفصل الأول through الفصل الرابع عشر
    ...
)
```

If `expected_chapter_headings = 0`, SHC factor is set to 1.0 (not penalized). This is correct for laws with no chapter structure (e.g., EG_ESIGN).
