import json as _json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

# ── Model ──────────────────────────────────────────────────────────────────────
PRIMARY_MODEL    = os.getenv("PRIMARY_MODEL", "gemini-3.5-flash")
EMBEDDING_MODEL  = "text-embedding-004"

# ── Extraction thresholds ──────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD = 0.85
PYMUPDF_MIN_CHARS    = 200

# ── Rate-limit cooldowns ───────────────────────────────────────────────────────
KEY_RPM_COOLDOWN_SECONDS = int(os.getenv("KEY_RPM_COOLDOWN_SECONDS", "65"))
KEY_RPD_COOLDOWN_SECONDS = int(os.getenv("KEY_RPD_COOLDOWN_SECONDS", "86400"))

# ── Data directories ───────────────────────────────────────────────────────────
DATA_DIR              = BASE_DIR / "data"
RAW_PDFS_DIR          = DATA_DIR / "raw_pdfs"
RAW_TXTS_DIR          = DATA_DIR / "raw_txts"
EXTRACTED_RAW_DIR     = DATA_DIR / "extracted_raw"
EXTRACTED_CLEAN_DIR   = DATA_DIR / "extracted_clean"
CLEANUP_AUDIT_DIR     = DATA_DIR / "cleanup_audit_logs"
SPLIT_ARTICLES_DIR    = DATA_DIR / "split_articles"
ENRICHED_ARTICLES_DIR = DATA_DIR / "enriched_articles"
CHUNKS_DIR            = DATA_DIR / "chunks"
HUMAN_REVIEW_DIR      = DATA_DIR / "human_review"


def _load_api_keys() -> list[str]:
    raw = os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.getenv("GEMINI_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


GEMINI_API_KEYS: list[str] = _load_api_keys()

# ── Pricing ────────────────────────────────────────────────────────────────────
# gemini-3.5-flash  — update when Google publishes official pricing
GEMINI_FLASH_INPUT_COST_PER_1M    = float(os.getenv("GEMINI_INPUT_COST",  "0.25"))
GEMINI_FLASH_OUTPUT_COST_PER_1M   = float(os.getenv("GEMINI_OUTPUT_COST", "1.00"))
GEMINI_FLASH_IMAGE_COST_PER_PAGE  = 0.00258

GEMINI_MAX_RETRIES       = 20
GEMINI_RETRY_BASE_DELAY  = 2.0

# ── Output token limit ─────────────────────────────────────────────────────────
# gemini-3.5-flash supports up to 65,536 output tokens.
# Setting the maximum gives the model full "breathing room" to respond completely.
# Override via MAX_OUTPUT_TOKENS env var if a different model with lower limits is used.
GEMINI_MAX_OUTPUT_TOKENS: int = int(os.getenv("MAX_OUTPUT_TOKENS", "65536"))

# ── Thinking configuration ─────────────────────────────────────────────────────
# Gemini supports two thinking control mechanisms (use the one matching your model):
#
# A) thinking_budget  (int) — for gemini-2.5-x models
#    0           → thinking disabled
#    N > 0       → up to N tokens used for internal reasoning
#    unset/None  → model auto-decides (usually up to 8,192 tokens)
#
# B) thinking_level  (str) — for gemini-3.x models (including gemini-3.5-flash)
#    "OFF"   → thinking disabled
#    "LOW"   → fast, light reasoning  (~0.3× cost of MEDIUM)
#    "MEDIUM"→ balanced              (recommended default for legal classification)
#    "HIGH"  → deep reasoning        (best accuracy, 3-5× more tokens)
#    unset   → model auto-decides
#
# Set ONE of the following per stage. Leave both empty to let the model decide.

def _parse_optional_int(env_key: str) -> int | None:
    raw = os.getenv(env_key, "").strip()
    return int(raw) if raw else None


# Stage 1 — OCR (pure extraction; thinking adds cost without benefit)
OCR_THINKING_BUDGET: int | None = _parse_optional_int("OCR_THINKING_BUDGET")
OCR_THINKING_LEVEL:  str | None = os.getenv("OCR_THINKING_LEVEL")   # e.g. "OFF"

# Stage 3 — Metadata Enrichment (legal categorisation benefits from some reasoning)
ENRICH_THINKING_BUDGET: int | None = _parse_optional_int("ENRICH_THINKING_BUDGET")
ENRICH_THINKING_LEVEL:  str | None = os.getenv("ENRICH_THINKING_LEVEL")  # e.g. "LOW"

# Stage 3.7 — Semantic Chunking (boundary decisions benefit from light reasoning)
CHUNK_THINKING_BUDGET: int | None = _parse_optional_int("CHUNK_THINKING_BUDGET")
CHUNK_THINKING_LEVEL:  str | None = os.getenv("CHUNK_THINKING_LEVEL")   # e.g. "LOW"

# ── Model rotation ─────────────────────────────────────────────────────────────
# When the primary model's daily quota (RPD) is exhausted across all keys,
# Stage 3 automatically falls back to FALLBACK_MODEL and vice-versa.
# Default mapping: each flash variant falls back to the other.
_FALLBACK_DEFAULTS = {
    "gemini-3.5-flash": "gemini-2.5-flash",
    "gemini-2.5-flash": "gemini-3.5-flash",
    "gemini-2.5-flash-preview-05-20": "gemini-3.5-flash",
    "gemini-3-flash-preview": "gemini-2.5-flash",
}
FALLBACK_MODEL: str = os.getenv(
    "FALLBACK_MODEL",
    _FALLBACK_DEFAULTS.get(os.getenv("PRIMARY_MODEL", "gemini-3.5-flash"), "gemini-2.5-flash"),
)

# ── Batch enrichment ───────────────────────────────────────────────────────────
# Number of articles sent per Gemini call.
# Default raised from 10 → 50 because TPM usage is only 3-9 K out of 250 K per
# request — packing 50 articles uses ~20 K output tokens (well within the 65 K
# max_output_tokens limit) and reduces total API calls from ~104 to ~19 for
# EG_CIVIL_CODE, easily fitting within the 20 RPD-per-key-per-model quota.
ENRICH_BATCH_SIZE: int = int(os.getenv("ENRICH_BATCH_SIZE", "200"))
# Raised from 150 → 200 to utilise more of the 250 K TPM limit.
# With 7 output fields (added concepts + applicable_to) per article ≈ 250 output tokens:
#   200 articles × 250 tokens = 50 K output tokens/call (< 65 K max)
# EG_CIVIL_CODE (1039 articles): 1039 / 200 = 6 API calls (was 7 at batch=150).

# ── Semantic chunking ──────────────────────────────────────────────────────────
# True → Gemini identifies semantic boundaries for long articles
# False (default) → rule-based splitting (free, no API calls)
SEMANTIC_CHUNKING: bool = os.getenv("SEMANTIC_CHUNKING", "false").lower() == "true"

# ── OCR Prompt ─────────────────────────────────────────────────────────────────
# Best practices applied:
# • System instruction kept separate (passed via system_instruction param)
# • Task stated FIRST (before document) + repeated at END (long-context sandwich)
# • Strict extraction-only mode — no thinking/reasoning needed
OCR_PROMPT = """\
Extract the complete text from this document. Follow the rules below exactly.

## Output Rules
- Output ONLY the document text in Markdown format
- Do NOT add any introduction, explanation, comment, or metadata
- Start directly with the document content

## Formatting
- Use `#`, `##`, `###` for headings
- Use `-` for bullet lists, `1.` for numbered lists
- Preserve paragraph breaks

## Accuracy Requirements
- Extract ALL text — zero omissions
- Keep original language exactly (Arabic, English, or mixed)
- Preserve Arabic legal article numbers: مادة 1, مادة 2, ...
- Preserve chapter/section headings: الباب الأول, الفصل الأول, ...
- Preserve clause sub-numbering: (أ), (ب), (ج) or (1), (2), (3)
- Keep Arabic diacritics if present
- Preserve Arabic-Indic numerals: ١، ٢، ٣ as-is
- Do NOT correct, translate, paraphrase, or interpret
- Do NOT skip pages, footnotes, tables, or headers

Now extract the complete document text:\
"""

OCR_SYSTEM_INSTRUCTION = """\
You are a professional document transcription specialist with expertise in Arabic legal documents.
Your sole task is verbatim text extraction — complete, accurate, and unmodified.
Never add commentary. Never skip content. Never interpret or summarize.\
"""
