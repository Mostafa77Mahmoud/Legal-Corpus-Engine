"""
Stage 1: Extraction (PDF or plain-text)

Strategy for PDF input:
  1. Attempt PyMuPDF native text extraction.
  2. Score extraction confidence (Stage 1.5 inline pre-check).
  3. If confidence >= threshold → save with source "pymupdf", skip Gemini.
  4. If confidence < threshold → fall back to Gemini OCR via File API.

Strategy for TXT input (when law_entry.txt_filename is set):
  1. Read the file directly — no PyMuPDF, no Gemini call.
  2. Strip website navigation boilerplate (masaar.net / aggregator headers).
  3. Save with source "plaintext".

Output written to: data/extracted_raw/{law_id}.txt
Metadata written to: data/extracted_raw/{law_id}_meta.json
"""

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import fitz

from config.law_registry import LawEntry
from config.settings import (
    CONFIDENCE_THRESHOLD,
    EXTRACTED_RAW_DIR,
    OCR_PROMPT,
    PRIMARY_MODEL,
    PYMUPDF_MIN_CHARS,
    RAW_TXTS_DIR,
)
from utils.arabic_text import (
    arabic_char_density,
    count_article_markers,
    count_structural_headings,
    replacement_char_density,
    strip_txt_boilerplate,
)
from utils.cost_tracker import CostTracker
from utils.llm_client import ocr_pdf

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    law_id: str
    extraction_source: str
    raw_text_path: str
    char_count: int
    page_count: int
    arabic_density: float
    replacement_density: float
    article_markers_found: int
    structural_headings_found: int
    extraction_model: str | None
    extraction_date: str
    success: bool
    error: str | None = None


_PRES_FORMS_B = re.compile(r"[\uFE70-\uFEFF]")
_PRES_FORMS_THRESHOLD = 0.01   # apply NFKC when > 1% of chars are presentation forms


def _normalize_presentation_forms(text: str) -> tuple[str, bool]:
    """
    Detect Arabic Presentation Forms (U+FE70-FEFF) and convert to standard
    Arabic (U+0600-U+06FF) via NFKC.

    Many older Egyptian law PDFs (pre-2010, Ministry of Finance) embed Arabic
    in the Presentation Forms block instead of standard Unicode.  PyMuPDF
    extracts this text correctly, but all downstream regex patterns (article
    marker counts, structural heading counts, Stage 2 splitter) only match
    standard Arabic codepoints.  Without normalization:
      - count_article_markers() returns 0  → amd_norm = 0
      - confidence collapses to ~0.28      → unnecessary Gemini OCR
    NFKC resolves all ligature encodings to canonical form.

    Returns (normalized_text, was_applied).
    """
    if not text:
        return text, False
    pres_count = len(_PRES_FORMS_B.findall(text))
    if pres_count / len(text) >= _PRES_FORMS_THRESHOLD:
        return unicodedata.normalize("NFKC", text), True
    return text, False


_MIN_DUPLICATE_HALF_MARKERS = 5  # require at least this many markers per half to trust the signal


def _detect_and_strip_full_duplication(text: str, law_id: str) -> tuple[str, dict | None]:
    """
    Detect whole-document duplication in extracted text and strip the
    duplicate second copy.

    Gemini OCR occasionally emits the entire document's text twice within a
    single response (a known LLM repetition failure mode for long verbatim
    extraction tasks) — the two copies are near-identical but not always
    byte-identical (minor OCR spelling/dash variance between the two
    generations), so a character-diff approach is unreliable. Instead this
    uses the article-marker sequence (the same hit-collector Stage 2 uses)
    as a structural fingerprint: if the ordered list of article numbers
    splits into two exactly-matching halves of roughly equal length, the
    text is almost certainly a full duplicate and the second half is
    dropped, keeping only the first (original) copy.

    This check runs unconditionally in Stage 1 (regardless of extraction
    source) because the failure mode is in the OCR generation, not in any
    per-law text — any law's Gemini OCR fallback could hit it.

    Returns (possibly-truncated text, info dict or None if no duplication found).
    """
    from pipeline.stage_2_split import _collect_hits

    hits = _collect_hits(text)
    n = len(hits)
    if n < _MIN_DUPLICATE_HALF_MARKERS * 2 or n % 2 != 0:
        return text, None

    half = n // 2
    first_numbers = [h.number for h in hits[:half]]
    second_numbers = [h.number for h in hits[half:]]
    if first_numbers != second_numbers:
        return text, None

    split_pos = hits[half].pos
    first_segment_len = split_pos
    second_segment_len = len(text) - split_pos
    # Sanity guard: the two segments should be roughly the same length —
    # a real duplication produces near-equal halves; a coincidental marker
    # repeat pattern in a genuinely different-length document should not.
    len_ratio = second_segment_len / max(1, first_segment_len)
    if not (0.6 <= len_ratio <= 1.5):
        return text, None

    truncated = text[:split_pos].rstrip()
    info = {
        "original_chars": len(text),
        "truncated_chars": len(truncated),
        "duplicate_marker_count": half,
        "split_position": split_pos,
    }
    logger.warning(
        "[%s] Detected full-document duplication in extracted text "
        "(%d markers repeated identically) — stripping second copy: "
        "%d chars → %d chars.",
        law_id, half, info["original_chars"], info["truncated_chars"],
    )
    return truncated, info


def _extract_pymupdf(pdf_path: Path) -> tuple[str, int]:
    doc = fitz.open(str(pdf_path))
    pages: list[str] = []
    for page in doc:
        pages.append(page.get_text("text"))
    page_count = len(doc)
    doc.close()
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    return text, page_count


def _quick_confidence(text: str, law_entry: LawEntry) -> float:
    if len(text) < PYMUPDF_MIN_CHARS:
        return 0.0

    acd = arabic_char_density(text)
    cs = replacement_char_density(text)
    cs_norm = max(0.0, 1.0 - (cs / 0.05))

    markers = count_article_markers(text)
    expected = law_entry.expected_article_count
    amd = markers / expected if expected > 0 else 0.0
    amd_norm = max(0.0, 1.0 - abs(1.0 - amd))

    eacc_norm = min(1.0, markers / expected) if expected > 0 else 0.0

    headings = count_structural_headings(text)
    expected_headings = law_entry.expected_chapter_headings
    shc_norm = min(1.0, headings / expected_headings) if expected_headings > 0 else 1.0

    confidence = (
        amd_norm * 0.25
        + acd * 0.25
        + eacc_norm * 0.25
        + shc_norm * 0.15
        + cs_norm * 0.10
    )
    return round(confidence, 4)


def run(
    pdf_path: Path,
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    force_ocr: bool = False,
) -> ExtractionResult:
    EXTRACTED_RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_txt = EXTRACTED_RAW_DIR / f"{law_entry.law_id}.txt"
    out_meta = EXTRACTED_RAW_DIR / f"{law_entry.law_id}_meta.json"

    page_count = 0
    extraction_source = "unknown"
    extraction_model: str | None = None
    raw_text = ""
    error: str | None = None

    try:
        # ── TXT shortcut: read directly, no PyMuPDF, no Gemini ──────────────
        if law_entry.txt_filename:
            txt_source = RAW_TXTS_DIR / law_entry.txt_filename
            if not txt_source.exists():
                raise FileNotFoundError(
                    f"TXT file not found: {txt_source}\n"
                    f"Place the file at: data/raw_txts/{law_entry.txt_filename}"
                )
            logger.info("[%s] Reading plain-text source: %s", law_entry.law_id, txt_source.name)
            raw_text = strip_txt_boilerplate(txt_source.read_text(encoding="utf-8"))
            extraction_source = "plaintext"
            logger.info("[%s] Boilerplate stripped — %d chars of law text", law_entry.law_id, len(raw_text))

        # ── PDF path ─────────────────────────────────────────────────────────
        else:
            if not pdf_path.exists():
                raise FileNotFoundError(
                    f"PDF not found: {pdf_path}\n"
                    f"Place the PDF at: data/raw_pdfs/{law_entry.pdf_filename}"
                )

            # ── Cache hit: skip extraction if raw output already exists ───────
            if not force_ocr and out_txt.exists() and out_meta.exists():
                meta = json.loads(out_meta.read_text(encoding="utf-8"))
                raw_text = out_txt.read_text(encoding="utf-8")
                extraction_source = meta.get("extraction_source", "cached")
                extraction_model = meta.get("extraction_model")
                page_count = meta.get("page_count", 0)
                logger.info(
                    "[%s] Cache hit — reusing %s extraction (%d chars). "
                    "Pass force_ocr=True to re-extract.",
                    law_entry.law_id, extraction_source, len(raw_text),
                )
            else:
                # No cache — run PyMuPDF then fall back to Gemini if needed
                pymupdf_confidence = 0.0
                if force_ocr:
                    logger.info("[%s] Force OCR mode — skipping PyMuPDF.", law_entry.law_id)
                else:
                    logger.info("[%s] Attempting PyMuPDF extraction…", law_entry.law_id)
                    pymupdf_text, page_count = _extract_pymupdf(pdf_path)
                    # Normalise Arabic Presentation Forms (U+FE70-FEFF) to
                    # standard Arabic BEFORE confidence scoring.  PDFs that
                    # use the legacy encoding (e.g. Ministry of Finance pre-2010)
                    # score ~0.28 without this (markers=0) and incorrectly
                    # trigger an expensive Gemini OCR call.
                    pymupdf_text, pres_applied = _normalize_presentation_forms(pymupdf_text)
                    if pres_applied:
                        logger.info(
                            "[%s] Arabic Presentation Forms detected — NFKC applied "
                            "to PyMuPDF output before confidence scoring.",
                            law_entry.law_id,
                        )
                    pymupdf_confidence = _quick_confidence(pymupdf_text, law_entry)
                    logger.info(
                        "[%s] PyMuPDF confidence: %.4f (threshold: %.2f)",
                        law_entry.law_id, pymupdf_confidence, CONFIDENCE_THRESHOLD,
                    )

                if not force_ocr and pymupdf_confidence >= CONFIDENCE_THRESHOLD:
                    logger.info("[%s] ✓ PyMuPDF meets threshold — no Gemini call needed.", law_entry.law_id)
                    raw_text = pymupdf_text
                    extraction_source = "pymupdf"
                else:
                    reason = "force_ocr" if force_ocr else f"low confidence ({pymupdf_confidence:.4f})"
                    logger.info("[%s] Falling back to Gemini OCR (%s)…", law_entry.law_id, reason)
                    raw_text = ocr_pdf(
                        pdf_path=pdf_path,
                        prompt=OCR_PROMPT,
                        cost_tracker=cost_tracker,
                        stage="stage_1",
                        law_id=law_entry.law_id,
                        model_name=PRIMARY_MODEL,
                    )
                    extraction_source = "gemini_ocr"
                    extraction_model = PRIMARY_MODEL
                    if page_count == 0:
                        try:
                            doc = fitz.open(str(pdf_path))
                            page_count = len(doc)
                            doc.close()
                        except Exception:
                            page_count = 0

        # Applies regardless of source (cache/pymupdf/gemini_ocr/plaintext) —
        # the repetition failure mode lives in OCR generation, so any cached
        # output from before this check existed is also corrected here,
        # with no extra Gemini cost since it works on already-extracted text.
        raw_text, dup_info = _detect_and_strip_full_duplication(raw_text, law_entry.law_id)
        if dup_info is not None:
            extraction_source = f"{extraction_source}+dedup_stripped"

        out_txt.write_text(raw_text, encoding="utf-8")
        logger.info("[%s] Raw text saved → %s (%d chars)", law_entry.law_id, out_txt.name, len(raw_text))

        result = ExtractionResult(
            law_id=law_entry.law_id,
            extraction_source=extraction_source,
            raw_text_path=str(out_txt),
            char_count=len(raw_text),
            page_count=page_count,
            arabic_density=round(arabic_char_density(raw_text), 4),
            replacement_density=round(replacement_char_density(raw_text), 4),
            article_markers_found=count_article_markers(raw_text),
            structural_headings_found=count_structural_headings(raw_text),
            extraction_model=extraction_model,
            extraction_date=datetime.utcnow().isoformat() + "Z",
            success=True,
        )

    except Exception as exc:
        logger.error("[%s] Extraction failed: %s", law_entry.law_id, exc)
        result = ExtractionResult(
            law_id=law_entry.law_id,
            extraction_source="failed",
            raw_text_path="",
            char_count=0,
            page_count=0,
            arabic_density=0.0,
            replacement_density=0.0,
            article_markers_found=0,
            structural_headings_found=0,
            extraction_model=None,
            extraction_date=datetime.utcnow().isoformat() + "Z",
            success=False,
            error=str(exc),
        )

    out_meta.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result
