# Stage 1 — Raw Extraction

**Status:** ✅ Complete — pilot-tested on EG_PDPL and EG_ESIGN  
**File:** `pipeline/stage_1_extract.py`  
**Runner:** `run_pilot.py`

---

## Purpose

Extract the complete raw text from each law's source file (PDF or TXT) and save it to `data/extracted_raw/{LAW_ID}.txt` for downstream stages.

---

## Strategy — Hybrid Extraction

```
Input: PDF or TXT
        │
        ├─ TXT source registered in law_registry? ──YES──▶ Read TXT directly
        │                                                    Strip masaar.net boilerplate
        │                                                    source = "plaintext"
        │
        └─ NO ──▶ PyMuPDF native extraction
                       │
                       └─ Quick confidence check (inline Stage 1.5)
                                │
                                ├─ score ≥ 0.85 ──▶ Save as-is
                                │                    source = "pymupdf"
                                │
                                └─ score < 0.85 ──▶ Gemini OCR via File API
                                                     Upload PDF → generateContent
                                                     source = "gemini_ocr"
```

**Why hybrid?** Egyptian laws range from 1948 (scanned microfilm) to 2020 (born-digital PDFs). Sending clean digital PDFs through Gemini OCR wastes API quota, adds latency, and risks hallucination. PyMuPDF handles born-digital PDFs perfectly at zero cost.

---

## Inputs

| Path | Description |
|------|-------------|
| `data/raw_pdfs/{pdf_filename}` | Source PDF (named per `law_registry.pdf_filename`) |
| `data/raw_txts/{txt_filename}` | Plain-text source (named per `law_registry.txt_filename`) |

Both source directories must exist before running. Copy source files from `Legla/` using the registered filenames (e.g., `EG_PDPL.pdf`, `EG_ESIGN.pdf`).

---

## Outputs

| File | Description |
|------|-------------|
| `data/extracted_raw/{LAW_ID}.txt` | Raw extracted text |
| `data/extracted_raw/{LAW_ID}_meta.json` | Extraction metadata |

### `_meta.json` Schema

```json
{
  "law_id": "EG_PDPL",
  "extraction_source": "plaintext",   // "pymupdf" | "gemini_ocr" | "plaintext"
  "raw_text_path": "/path/to/EG_PDPL.txt",
  "char_count": 37003,
  "page_count": 0,                    // 0 for TXT sources
  "arabic_density": 0.8114,
  "replacement_density": 0.0,
  "article_markers_found": 56,
  "structural_headings_found": 24,
  "extraction_model": null,           // model name if Gemini OCR was used
  "extraction_date": "2026-06-21T13:13:28Z",
  "success": true,
  "error": null
}
```

---

## TXT Boilerplate Stripping

Laws sourced from `masaar.net` (saved as TXT) include website navigation headers and Creative Commons footers. `utils/arabic_text.strip_txt_boilerplate()` crops the text to the law content using these markers:

**Start markers** (first match wins):
- `نص التشريع`
- `نص القانون`
- `نص اللائحة`

**End markers** (first match wins):
- `Creative Commons`
- `← قانون`
- `سياسة الخصوصية`
- `\nIcons and photos`

If no markers are found, the full text is returned unchanged.

---

## Gemini OCR Details

- **Model:** `gemini-3.5-flash` (configurable via `PRIMARY_MODEL` env var)
- **Method:** File API upload → `generateContent` with PDF + OCR prompt
- **Key pinning:** The same API key is used for upload AND generation. Files uploaded with key A cannot be read by key B. If a 429 occurs during generation, the file is deleted and re-uploaded with the new key.
- **Max output tokens:** 65,536

See `docs/05_INFRASTRUCTURE.md` for key rotation and rate-limit handling.

---

## ExtractionResult Dataclass

```python
@dataclass
class ExtractionResult:
    law_id: str
    extraction_source: str       # "pymupdf" | "gemini_ocr" | "plaintext"
    raw_text_path: str
    char_count: int
    page_count: int
    arabic_density: float
    replacement_density: float
    article_markers_found: int
    structural_headings_found: int
    extraction_model: str | None
    extraction_date: str         # ISO 8601 UTC
    success: bool
    error: str | None
```

---

## Known Edge Cases

| Law | Issue | Resolution |
|-----|-------|-----------|
| EG_ESIGN | Arabic ligature encoding defect in PDF | PyMuPDF confidence = 0.73 → Gemini OCR fallback |
| EG_PDPL | masaar.net TXT has website boilerplate | Stripped automatically by `strip_txt_boilerplate()` |

---

## Running

```bash
python run_pilot.py EG_PDPL     # uses TXT source directly
python run_pilot.py EG_ESIGN    # PyMuPDF fails → Gemini OCR
```

Stage 1 and Stage 1.5 run together in `run_pilot.py`. Stage 1.3 (cleanup) is not yet implemented and will be inserted between Stage 1 and 1.5 in the full pipeline runner.
