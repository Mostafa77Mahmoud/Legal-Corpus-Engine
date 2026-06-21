import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "gemini-3.5-flash")
EMBEDDING_MODEL = "text-embedding-004"

CONFIDENCE_THRESHOLD = 0.85
PYMUPDF_MIN_CHARS = 200

# Rate-limit cooldown durations
KEY_RPM_COOLDOWN_SECONDS = int(os.getenv("KEY_RPM_COOLDOWN_SECONDS", "65"))    # per-minute quota: wait ~1 min
KEY_RPD_COOLDOWN_SECONDS = int(os.getenv("KEY_RPD_COOLDOWN_SECONDS", "86400")) # per-day quota: wait 24 h

DATA_DIR = BASE_DIR / "data"
RAW_PDFS_DIR = DATA_DIR / "raw_pdfs"
RAW_TXTS_DIR = DATA_DIR / "raw_txts"
EXTRACTED_RAW_DIR = DATA_DIR / "extracted_raw"
EXTRACTED_CLEAN_DIR = DATA_DIR / "extracted_clean"
CLEANUP_AUDIT_DIR = DATA_DIR / "cleanup_audit_logs"


def _load_api_keys() -> list[str]:
    raw = os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.getenv("GEMINI_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


GEMINI_API_KEYS: list[str] = _load_api_keys()

# gemini-3.5-flash pricing (as of 2025) — https://ai.google.dev/gemini-api/docs/models
GEMINI_FLASH_INPUT_COST_PER_1M = 0.25    # USD per 1M input tokens (non-thinking)
GEMINI_FLASH_OUTPUT_COST_PER_1M = 1.00   # USD per 1M output tokens (non-thinking)
GEMINI_FLASH_IMAGE_COST_PER_PAGE = 0.00258

GEMINI_MAX_RETRIES = 20
GEMINI_RETRY_BASE_DELAY = 2.0

OCR_PROMPT = """You are an expert document processing AI specialized in extracting text from PDF and TXT files with maximum accuracy.

## Your Task

Extract the **complete text** from the provided file with:
- **High accuracy**: Every word, number, and character must be captured
- **Proper structure**: Preserve the document's logical structure
- **Format preservation**: Maintain visual hierarchy and organization

## Extraction Rules

### 1. Format Guidelines

Use **Markdown** format to preserve structure:
- **Headings**: Use `#`, `##`, `###` for different heading levels
- **Lists**: Use `*` or `-` for bullet points, `1.`, `2.` for numbered lists
- **Bold/Italic**: Use `**bold**` and `*italic*` where appropriate

### 2. Content Rules

**MUST DO:**
- Extract ALL text without omission
- Keep original text exactly as written (no corrections, no changes)
- Preserve original language (Arabic, English, or mixed)
- Maintain paragraph breaks and spacing
- Capture table contents accurately

**MUST NOT DO:**
- Add comments, explanations, or annotations
- Translate or modify the text
- Skip sections or pages
- Summarize or paraphrase
- Add your own interpretations
- Remove or ignore any text elements

### 3. Arabic Legal Documents

- Extract in Arabic with proper RTL consideration
- Preserve Arabic diacritics if present
- Maintain Arabic numbering styles (١، ٢، ٣)
- Preserve legal article numbering exactly: مادة 1, مادة 2, ...
- Keep chapter and section headings: الباب الأول, الفصل الأول, ...
- Preserve clause sub-numbering: (أ), (ب), (ج) or (1), (2), (3)
- Keep legal terminology intact — do NOT paraphrase or simplify

### 4. Special Cases

**Legal Documents:**
- Preserve clause numbering exactly (مادة 1, مادة 1.1, ...)
- Keep legal terminology intact
- Maintain contract structure

**Arabic Documents:**
- Keep Arabic legal terminology exact
- Maintain Arabic numbering (١، ٢، ٣) if used

## Quality Standards

Your extraction must achieve:
- **Completeness**: 100% of text captured
- **Accuracy**: 99%+ character-level accuracy
- **Structure**: Original document structure preserved

## Output Format

**Output ONLY the extracted Markdown text**

Do NOT include:
- Explanations before the text
- Comments like "Here is the extraction:"
- Notes about extraction quality
- Summary of the document
- Metadata or file information

**Start directly with the document content in Markdown format**
"""
