"""
Stage 1: PDF Extraction

Strategy:
  1. Attempt PyMuPDF native text extraction.
  2. Score extraction confidence (Stage 1.5 inline pre-check).
  3. If confidence >= threshold → save with source "pymupdf", skip Gemini.
  4. If confidence < threshold → fall back to Gemini OCR via File API.

Output written to: data/extracted_raw/{law_id}.txt
Metadata written to: data/extracted_raw/{law_id}_meta.json
"""

import json
import logging
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
)
from utils.arabic_text import (
    arabic_char_density,
    count_article_markers,
    count_structural_headings,
    replacement_char_density,
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

    if not pdf_path.exists():
        raise FileNotFoundError(
            f"PDF not found: {pdf_path}\n"
            f"Place the PDF at: data/raw_pdfs/{law_entry.pdf_filename}"
        )

    page_count = 0
    extraction_source = "unknown"
    extraction_model: str | None = None
    raw_text = ""
    error: str | None = None

    try:
        if force_ocr:
            logger.info("[%s] Force OCR mode — skipping PyMuPDF.", law_entry.law_id)
            pymupdf_confidence = 0.0
        else:
            logger.info("[%s] Attempting PyMuPDF extraction…", law_entry.law_id)
            pymupdf_text, page_count = _extract_pymupdf(pdf_path)
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
