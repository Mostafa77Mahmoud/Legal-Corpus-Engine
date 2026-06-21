"""
Stage 1.5: Extraction Quality & Confidence Scoring

Runs full 5-factor confidence scoring on the raw extracted text.
Flags the document for human review if confidence < CONFIDENCE_THRESHOLD.

Confidence formula:
    Confidence = (AMD_norm × 0.25) + (ACD_norm × 0.25) + (EACC_norm × 0.25)
               + (SHC_norm × 0.15) + (CS_norm × 0.10)
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from config.law_registry import LawEntry
from config.settings import CONFIDENCE_THRESHOLD, EXTRACTED_CLEAN_DIR, EXTRACTED_RAW_DIR
from utils.arabic_text import (
    arabic_char_density,
    count_article_markers,
    count_structural_headings,
    replacement_char_density,
)

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceReport:
    law_id: str
    extraction_source: str
    char_count: int
    article_markers_found: int
    expected_article_count: int
    structural_headings_found: int
    expected_chapter_headings: int

    acd: float
    amd: float
    eacc: float
    shc: float
    cs: float

    acd_norm: float
    amd_norm: float
    eacc_norm: float
    shc_norm: float
    cs_norm: float

    confidence_score: float
    threshold: float
    passed: bool
    manual_review: bool
    scored_at: str

    @property
    def factor_breakdown(self) -> dict:
        return {
            "ACD (Arabic character density)": {"raw": self.acd, "norm": self.acd_norm, "weight": 0.25, "contribution": round(self.acd_norm * 0.25, 4)},
            "AMD (article marker density)": {"raw": self.amd, "norm": self.amd_norm, "weight": 0.25, "contribution": round(self.amd_norm * 0.25, 4)},
            "EACC (expected article count coverage)": {"raw": self.eacc, "norm": self.eacc_norm, "weight": 0.25, "contribution": round(self.eacc_norm * 0.25, 4)},
            "SHC (structural heading coverage)": {"raw": self.shc, "norm": self.shc_norm, "weight": 0.15, "contribution": round(self.shc_norm * 0.15, 4)},
            "CS (corruption score — inverted)": {"raw": self.cs, "norm": self.cs_norm, "weight": 0.10, "contribution": round(self.cs_norm * 0.10, 4)},
        }


def run(law_entry: LawEntry, extraction_source: str = "unknown") -> ConfidenceReport:
    # Prefer cleaned text (Stage 1.3 output); fall back to raw if cleanup hasn't run yet
    txt_path = EXTRACTED_CLEAN_DIR / f"{law_entry.law_id}.txt"
    if not txt_path.exists():
        txt_path = EXTRACTED_RAW_DIR / f"{law_entry.law_id}.txt"
    if not txt_path.exists():
        raise FileNotFoundError(
            f"No text found at {EXTRACTED_CLEAN_DIR / law_entry.law_id}.txt "
            f"or {EXTRACTED_RAW_DIR / law_entry.law_id}.txt. Run Stage 1 first."
        )

    text = txt_path.read_text(encoding="utf-8")

    acd = round(arabic_char_density(text), 4)
    cs = round(replacement_char_density(text), 4)

    markers = count_article_markers(text)
    expected = law_entry.expected_article_count
    amd = round(markers / expected, 4) if expected > 0 else 0.0

    eacc = round(min(1.0, markers / expected), 4) if expected > 0 else 0.0

    headings = count_structural_headings(text)
    expected_headings = law_entry.expected_chapter_headings
    shc = round(min(1.0, headings / expected_headings), 4) if expected_headings > 0 else 1.0

    amd_norm = round(max(0.0, 1.0 - abs(1.0 - amd)), 4)
    acd_norm = acd
    eacc_norm = eacc
    shc_norm = shc
    cs_norm = round(max(0.0, 1.0 - (cs / 0.05)), 4)

    confidence = round(
        amd_norm * 0.25
        + acd_norm * 0.25
        + eacc_norm * 0.25
        + shc_norm * 0.15
        + cs_norm * 0.10,
        4,
    )

    passed = confidence >= CONFIDENCE_THRESHOLD
    manual_review = not passed

    report = ConfidenceReport(
        law_id=law_entry.law_id,
        extraction_source=extraction_source,
        char_count=len(text),
        article_markers_found=markers,
        expected_article_count=expected,
        structural_headings_found=headings,
        expected_chapter_headings=expected_headings,
        acd=acd,
        amd=amd,
        eacc=eacc,
        shc=shc,
        cs=cs,
        acd_norm=acd_norm,
        amd_norm=amd_norm,
        eacc_norm=eacc_norm,
        shc_norm=shc_norm,
        cs_norm=cs_norm,
        confidence_score=confidence,
        threshold=CONFIDENCE_THRESHOLD,
        passed=passed,
        manual_review=manual_review,
        scored_at=datetime.utcnow().isoformat() + "Z",
    )

    report_path = EXTRACTED_RAW_DIR / f"{law_entry.law_id}_confidence.json"
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "[%s] Confidence: %.4f (%s) — manual_review=%s",
        law_entry.law_id,
        confidence,
        "PASS" if passed else "FAIL",
        manual_review,
    )
    return report
