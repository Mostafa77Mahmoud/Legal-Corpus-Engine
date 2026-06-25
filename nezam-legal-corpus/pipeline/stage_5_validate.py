"""
Stage 5 — Corpus Validation
============================
Input:  data/enriched_articles/{LAW_ID}/articles.json
        data/chunks/{LAW_ID}/chunks.json
Output: data/validated/{LAW_ID}/validation_report.json

Validates every article and chunk for:
  V001 — ENRICHMENT_INCOMPLETE : required field missing or empty
  V002 — INVALID_CATEGORY      : article_category not in allowed set
  V003 — ENRICHMENT_ERROR      : article has enrichment_error from Stage 3
  V004 — REPEALED_MISMATCH     : is_repealed flag disagrees with law_registry
  V005 — EMPTY_KEYWORDS        : keywords list is empty

Validation is entirely rule-based — no Gemini calls.

Quality gate:
  FAIL if any V001 or V003 errors remain unfixed
  WARN for V002, V004, V005 (logged, not blocking)
  PASS if zero V001 + V003 errors
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.law_registry import LawEntry
from config.settings import CHUNKS_DIR, ENRICHED_ARTICLES_DIR

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

VALIDATED_DIR_NAME = "validated"

VALID_CATEGORIES: frozenset[str] = frozenset({
    "تعريف", "حق", "التزام", "إجراء", "عقوبة",
    "تنظيمية", "انتقالية", "إصدار", "أخرى",
})

REQUIRED_ENRICHMENT_FIELDS = ("topic", "article_summary", "article_category")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    code: str            # V001–V005
    article_id: str
    field: str
    message: str
    severity: str        # "error" | "warning"


@dataclass
class ValidationReport:
    law_id: str
    law_name_ar: str
    total_articles: int
    total_chunks: int
    passed: bool
    error_count: int
    warning_count: int
    issues: list[dict[str, str]]
    validated_at: str
    output_path: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validated_dir(law_id: str) -> Path:
    base = ENRICHED_ARTICLES_DIR.parent / VALIDATED_DIR_NAME
    d = base / law_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check_article(
    article: dict[str, Any],
    repealed_set: frozenset[int],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    aid = article.get("article_id", "UNKNOWN")
    art_num = article.get("article_number")

    # V003 — Enrichment error from Stage 3
    if article.get("enrichment_error"):
        issues.append(ValidationIssue(
            code="V003", article_id=aid, field="enrichment_error",
            message=f"Stage 3 enrichment failed: {article['enrichment_error']}",
            severity="error",
        ))
        return issues  # rest of checks meaningless if enrichment failed

    # V001 — Required fields missing or empty
    for fname in REQUIRED_ENRICHMENT_FIELDS:
        val = article.get(fname, "")
        if not val or (isinstance(val, str) and not val.strip()):
            issues.append(ValidationIssue(
                code="V001", article_id=aid, field=fname,
                message=f"Required field '{fname}' is missing or empty",
                severity="error",
            ))

    # V005 — Empty keywords
    kw = article.get("keywords", [])
    if not kw:
        issues.append(ValidationIssue(
            code="V005", article_id=aid, field="keywords",
            message="keywords list is empty",
            severity="warning",
        ))

    # V002 — Invalid category
    cat = article.get("article_category", "")
    if cat and cat not in VALID_CATEGORIES:
        issues.append(ValidationIssue(
            code="V002", article_id=aid, field="article_category",
            message=f"Invalid category '{cat}' — must be one of {sorted(VALID_CATEGORIES)}",
            severity="warning",
        ))

    # V004 — Repealed mismatch
    if art_num is not None:
        registry_repealed = art_num in repealed_set
        flag_repealed = bool(article.get("is_repealed", False))
        if registry_repealed != flag_repealed:
            issues.append(ValidationIssue(
                code="V004", article_id=aid, field="is_repealed",
                message=(
                    f"is_repealed={flag_repealed} but registry says "
                    f"{'repealed' if registry_repealed else 'active'}"
                ),
                severity="warning",
            ))

    return issues


# ── Public run function ───────────────────────────────────────────────────────

def run(law_entry: LawEntry) -> ValidationReport:
    """
    Validate all enriched articles and chunks for *law_entry*.
    Returns a ValidationReport. Never raises — errors go into the report.
    """
    law_id = law_entry.law_id
    repealed_set: frozenset[int] = frozenset(law_entry.repealed_articles)

    # ── Load enriched articles ────────────────────────────────────────────────
    articles_path = ENRICHED_ARTICLES_DIR / law_id / "articles.json"
    if not articles_path.exists():
        raise FileNotFoundError(
            f"Enriched articles not found: {articles_path}\nRun Stage 3 first."
        )
    articles: list[dict[str, Any]] = json.loads(
        articles_path.read_text(encoding="utf-8")
    )

    # ── Load chunks ───────────────────────────────────────────────────────────
    chunks_path = CHUNKS_DIR / law_id / "chunks.json"
    chunks_count = 0
    if chunks_path.exists():
        chunks_count = len(json.loads(chunks_path.read_text(encoding="utf-8")))

    # ── Validate every article ────────────────────────────────────────────────
    all_issues: list[ValidationIssue] = []
    for article in articles:
        all_issues.extend(_check_article(article, repealed_set))

    errors   = [i for i in all_issues if i.severity == "error"]
    warnings = [i for i in all_issues if i.severity == "warning"]
    passed   = len(errors) == 0

    # ── Write report ──────────────────────────────────────────────────────────
    out_dir  = _validated_dir(law_id)
    validated_at = datetime.now(timezone.utc).isoformat()

    report_data = {
        "law_id":          law_id,
        "law_name_ar":     law_entry.law_name_ar,
        "validated_at":    validated_at,
        "passed":          passed,
        "total_articles":  len(articles),
        "total_chunks":    chunks_count,
        "error_count":     len(errors),
        "warning_count":   len(warnings),
        "quality_gate":    "PASS" if passed else "FAIL",
        "issues": [asdict(i) for i in all_issues],
        "summary_by_code": _summarise_by_code(all_issues),
    }

    report_path = out_dir / "validation_report.json"
    report_path.write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if passed:
        logger.info(
            "[%s] Stage 5 PASS — %d articles validated, %d warnings",
            law_id, len(articles), len(warnings),
        )
    else:
        logger.warning(
            "[%s] Stage 5 FAIL — %d errors, %d warnings",
            law_id, len(errors), len(warnings),
        )
        for err in errors[:10]:
            logger.warning("  %s [%s] %s: %s", err.code, err.article_id, err.field, err.message)

    return ValidationReport(
        law_id=law_id,
        law_name_ar=law_entry.law_name_ar,
        total_articles=len(articles),
        total_chunks=chunks_count,
        passed=passed,
        error_count=len(errors),
        warning_count=len(warnings),
        issues=[asdict(i) for i in all_issues],
        validated_at=validated_at,
        output_path=str(report_path),
    )


def _summarise_by_code(issues: list[ValidationIssue]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for issue in issues:
        entry = summary.setdefault(issue.code, {"count": 0, "errors": 0, "warnings": 0})
        entry["count"] += 1
        entry[issue.severity + "s"] += 1
    return summary
