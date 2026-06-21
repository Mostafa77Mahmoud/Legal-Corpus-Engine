# Nezam Legal Corpus — Project Overview

## What Is This Project?

**Nezam Legal Corpus** is an automated pipeline for building a high-quality, structured digital corpus of Egyptian laws. The output is machine-readable legal data designed to power Arabic legal AI applications — specifically contract analysis, legal retrieval, and compliance checking.

The pipeline takes raw PDF or TXT sources of Egyptian laws, extracts their text, cleans it, splits it into individual articles, enriches each article with metadata (keywords, concepts, cross-references), chunks the articles for embedding, then assembles and exports everything to MongoDB and JSON.

---

## Architecture — 9-Stage Pipeline

```
PDF / TXT
    │
    ▼
Stage 1      Raw Extraction
             PyMuPDF (native) → confidence check → Gemini OCR fallback
    │
    ▼
Stage 1.3    Arabic Cleanup
             Normalize Unicode, strip OCR artifacts, audit log diffs
    │
    ▼
Stage 1.5    Confidence Scoring
             5-factor quality gate (threshold 0.85) — flags for human review
    │
    ▼
Stage 2      Article Splitting
             Regex-first → LLM fallback — splits raw text into individual مادة records
    │
    ▼
Stage 2.5    Split Validation
             6-code error taxonomy, continuity checks, sequence gap detection
    │
    ▼
Stage 3      Metadata Enrichment
             Keywords, legal concepts, explicit cross-references, article_type
    │
    ▼
Stage 3.7    Chunking
             Paragraph-first, 500-token ceiling, no overlap
    │
    ▼
Stage 4      Human Review Export
             JSON review queue sorted by priority score
    │
    ▼
Stage 5      Rule-Based Validation + LLM Re-Validation
             ERROR_INVALID_REFERENCE gets LLM re-check
    │
    ▼
Stage 6      Assembly
             Deduplication, is_current_version / is_repealed resolution
    │
    ▼
Stage 7      Export
             MongoDB (egyptian_law_articles + egyptian_law_chunks) + JSON
    │
    ▼
Post-Export  Embeddings (separate script)
             scripts/generate_embeddings.py — text-embedding-004
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 |
| PDF Extraction | PyMuPDF (fitz) |
| OCR / LLM | Google Gemini (`gemini-3.5-flash`) via `google-genai` SDK |
| Embeddings | `text-embedding-004` |
| Database | MongoDB (2 collections) |
| API Key Management | Custom pool with RPM/RPD/permanent-disable logic |
| Config | `settings.py`, `law_registry.py`, `taxonomy.py` |
| Entry Point | `run_pilot.py` |

---

## Directory Structure

```
nezam-legal-corpus/
├── config/
│   ├── law_registry.py       # Registry of all 10 Egyptian laws
│   ├── settings.py           # Global constants, model, paths, rate limits
│   └── taxonomy.py           # Legal concept taxonomy
├── data/
│   ├── raw_pdfs/             # Source PDFs (EG_ESIGN.pdf, EG_PDPL.pdf, ...)
│   ├── raw_txts/             # Pre-extracted TXT sources (EG_PDPL.txt)
│   ├── extracted_raw/        # Stage 1+1.5 outputs (.txt, _meta.json, _confidence.json)
│   ├── extracted_clean/      # Stage 1.3 outputs
│   └── cleanup_audit_logs/   # Stage 1.3 diff logs
├── pipeline/
│   ├── stage_1_extract.py    ✅ DONE
│   ├── stage_1_5_val_extract.py ✅ DONE
│   └── [stages 1.3, 2–7 — NOT YET BUILT]
├── utils/
│   ├── arabic_text.py        # Arabic normalization, article marker detection
│   ├── cost_tracker.py       # Token usage + USD cost tracking
│   ├── key_manager.py        # Gemini API key pool (RPM/RPD/permanent cooldowns)
│   └── llm_client.py        # Gemini wrapper with key pinning + retry logic
├── tests/
│   ├── test_arabic_text.py
│   └── test_key_manager.py
├── docs/                     # ← This documentation
└── run_pilot.py              # CLI entry point
```

---

## Laws Registry

| ID | Law | Articles | PDF Type | Status |
|----|-----|----------|---------|--------|
| EG_PDPL | قانون حماية البيانات الشخصية (151/2020) | 56 | Digital + TXT | ✅ Pilot done |
| EG_ESIGN | قانون التوقيع الإلكتروني (15/2004) | 32 | Digital (ligature defect) | ✅ Pilot done |
| EG_EVIDENCE | قانون الإثبات (25/1968) | 99 | Mixed | ⏳ Next |
| EG_LABOR | قانون العمل (12/2003) | 254 | Mixed | ⏳ Queued |
| EG_RENT | قانون إيجار الأماكن (136/1981) | 80 | Scanned | ⏳ Queued |
| EG_CIVIL_PROCEDURE | قانون المرافعات (13/1968) | 480+ | Mixed | ⏳ Queued |
| EG_COMMERCIAL | قانون التجارة (17/1999) | 700 | Mixed | ⏳ Queued |
| EG_CIVIL_CODE | القانون المدني (131/1948) | 686 | Mixed | ⏳ Queued |
| EG_PENAL | قانون العقوبات (58/1937) | 535+ | Scanned | ⏳ Queued |
| EG_IP | قانون الملكية الفكرية (82/2002) | 188 | Digital | ⏳ Queued |

---

## Current Status (as of 2026-06-21)

**Completed:** Stage 1 + Stage 1.5 — pilot-tested on EG_PDPL and EG_ESIGN ✅

**In Progress:** Stage 1.3 (Arabic Cleanup) — next to build

**See:** `docs/04_PILOT_RESULTS.md` for detailed pilot output metrics.

---

## How to Run

```bash
# Install dependencies
pip install -r requirements.txt

# Run Stage 1+1.5 on a specific law
python run_pilot.py EG_PDPL
python run_pilot.py EG_ESIGN

# Available law IDs:
# EG_PDPL, EG_ESIGN, EG_EVIDENCE, EG_LABOR, EG_RENT,
# EG_CIVIL_PROCEDURE, EG_COMMERCIAL, EG_CIVIL_CODE, EG_PENAL, EG_IP
```

**Required secrets:**
- `GEMINI_API_KEYS` — comma-separated list of Gemini API keys (4 keys recommended)
- `PRIMARY_MODEL` — defaults to `gemini-3.5-flash` (can override via env)

---

## Quality Gates

| Gate | Metric | Threshold |
|------|--------|-----------|
| Extraction confidence | 5-factor score | ≥ 0.85 |
| Article split F1 | Golden benchmark | ≥ 0.98 |
| Explicit reference F1 | Golden benchmark | ≥ 0.95 |

MongoDB collections: `egyptian_law_articles`, `egyptian_law_chunks`
