---
name: Stage-1 extraction cache
description: Cache hit logic in stage_1_extract.py to skip Gemini OCR when output already exists
---

## Rule
If `out_txt` (extracted_raw/LAW_ID.txt) and `out_meta` (extracted_raw/LAW_ID_meta.json) both exist and `force_ocr=False`, stage_1 reads them directly and skips all extraction (PyMuPDF + Gemini).

**Why:** Gemini OCR costs API quota (20 RPD × 4 keys = 80/day). Re-running pilots should not burn quota. The cache avoids redundant calls.

**How to apply:**
- Pass `force_ocr=True` to `stage_1_extract.run()` to bypass cache and re-run OCR
- Cache is keyed on `EXTRACTED_RAW_DIR / f"{law_entry.law_id}.txt"` — if law_id changes, cache misses
- The `else` block (no cache) initializes `pymupdf_confidence = 0.0` before the PyMuPDF attempt
