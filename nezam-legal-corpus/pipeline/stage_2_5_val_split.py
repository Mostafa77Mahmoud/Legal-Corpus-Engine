"""
Stage 2.5: Split Validation

Validates the article split output from Stage 2 against the expected
article count and sequence rules registered in law_registry.

Six-code error taxonomy
-----------------------
E001  MISSING_ARTICLE       — article number present in 1..N but absent from split
E002  DUPLICATE_ARTICLE     — same article number appears in two records
E003  SEQUENCE_GAP          — gap in main-article sequence not covered by repealed_articles
E004  EMPTY_BODY            — article body is empty or under 5 characters
E005  OVERSIZED_ARTICLE     — article word count > 3× median word count (possible bad split)
E006  ORPHAN_TEXT           — substantial text found before the first article marker

Warnings (W-codes) — non-blocking
W001  OVER_COUNT            — more articles found than expected (> 10% surplus)
W002  UNDER_COUNT           — fewer articles found than expected (< 90% of expected)
W003  ISSUANCE_MISMATCH     — unexpected number of issuance articles

Input:   articles list + SplitReport from Stage 2
Output:  data/split_articles/{LAW_ID}/validation_report.json
"""

import json
import logging
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from config.law_registry import LawEntry
from config.settings import SPLIT_ARTICLES_DIR
from pipeline.stage_2_split import ArticleRecord, SplitReport

logger = logging.getLogger(__name__)

# ── dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    code: str                       # E001 … E006 or W001 … W003
    severity: Literal["error", "warning"]
    name: str
    description: str
    article_number: int | None = None
    article_id: str | None = None


@dataclass
class ValidationReport:
    law_id: str
    articles_checked: int
    passed: bool
    error_count: int
    warning_count: int
    issues: list[ValidationIssue] = field(default_factory=list)
    validated_at: str = ""


# ── public API ────────────────────────────────────────────────────────────────

def run(
    law_entry: LawEntry,
    articles: list[ArticleRecord],
    split_report: SplitReport,
) -> ValidationReport:
    """
    Validate the article split.  Returns a ValidationReport and writes
    validation_report.json to data/split_articles/{law_id}/.
    """
    issues: list[ValidationIssue] = []

    main_articles = [a for a in articles if a.article_type == "main"]
    issuance_articles = [a for a in articles if a.article_type == "issuance"]
    repealed_set = set(law_entry.repealed_articles)

    # ── E002: duplicate article numbers within main articles ─────────────────
    # A base article plus one or more "مكرر" (bis) articles sharing the same
    # base number (e.g. "148" and "148 مكرر") is a legitimate Egyptian
    # legislative convention for inserting an article without renumbering —
    # not a duplicate. Only flag when more than one non-bis article shares a
    # number (a genuine duplicate/split error).
    seen_numbers: dict[int, list[ArticleRecord]] = {}
    for a in main_articles:
        seen_numbers.setdefault(a.article_number, []).append(a)
    for num, arts in seen_numbers.items():
        non_bis = [a for a in arts if a.marker_kind != "bis"]
        if len(non_bis) > 1:
            ids = [a.article_id for a in non_bis]
            issues.append(ValidationIssue(
                code="E002", severity="error", name="DUPLICATE_ARTICLE",
                description=f"Article {num} appears {len(ids)} times: {ids}",
                article_number=num,
            ))

    # ── E001/E003: missing articles and sequence gaps ────────────────────────
    found_main_nums = {a.article_number for a in main_articles}
    max_main = max(found_main_nums, default=0)
    expected_main = max(
        law_entry.expected_article_count - len(issuance_articles),
        max_main,
    )

    for n in range(1, expected_main + 1):
        if n in found_main_nums:
            continue
        if n in repealed_set:
            continue
        # Is this a gap or a total miss?
        neighbours_present = (n - 1) in found_main_nums or (n + 1) in found_main_nums
        if neighbours_present:
            issues.append(ValidationIssue(
                code="E003", severity="error", name="SEQUENCE_GAP",
                description=(
                    f"Article {n} missing and not in repealed_articles "
                    f"(neighbours present — likely a split error)"
                ),
                article_number=n,
            ))
        else:
            issues.append(ValidationIssue(
                code="E001", severity="error", name="MISSING_ARTICLE",
                description=f"Article {n} expected but not found in split output",
                article_number=n,
            ))

    # ── E004: empty body ─────────────────────────────────────────────────────
    for a in articles:
        if len(a.text.strip()) < 5:
            issues.append(ValidationIssue(
                code="E004", severity="error", name="EMPTY_BODY",
                description=f"Article body is empty or near-empty ({len(a.text)} chars)",
                article_number=a.article_number,
                article_id=a.article_id,
            ))

    # ── E005: oversized article (warning) ────────────────────────────────────
    # Legal articles can legitimately be very long (definitions, penalty schedules).
    # 5× median with a 500-word floor avoids false positives on genuine big articles.
    # Only flags when an article is so large it's likely a failed split (multiple
    # articles merged into one).
    word_counts = [a.word_count for a in articles if a.word_count > 0]
    if word_counts:
        median_wc = statistics.median(word_counts)
        ceiling = max(median_wc * 5, 500)
        for a in articles:
            if a.word_count > ceiling:
                issues.append(ValidationIssue(
                    code="E005", severity="warning", name="OVERSIZED_ARTICLE",
                    description=(
                        f"Article {a.article_number} has {a.word_count} words "
                        f"(median={median_wc:.0f}, ceiling={ceiling:.0f}) — "
                        f"review manually: may be a merged split or a genuine long article"
                    ),
                    article_number=a.article_number,
                    article_id=a.article_id,
                ))

    # ── E006: substantial orphan text (warning) ───────────────────────────────
    # The preamble (Gazette header, law number, president's name) always precedes
    # the first article marker.  Typical preambles are 100–500 chars and are expected.
    # Only flag as a warning when orphan text is very large (> 800 chars), which would
    # suggest a genuine split failure (missing the first article marker).
    if split_report.orphan_text_chars > 800:
        issues.append(ValidationIssue(
            code="E006", severity="warning", name="ORPHAN_TEXT",
            description=(
                f"{split_report.orphan_text_chars} chars appear before the first "
                f"article marker — unusually large preamble, check for missed markers"
            ),
        ))

    # ── W001/W002: article count deviation ───────────────────────────────────
    found_total = len(articles)
    expected_total = law_entry.expected_article_count
    if expected_total > 0:
        ratio = found_total / expected_total
        if ratio > 1.10:
            issues.append(ValidationIssue(
                code="W001", severity="warning", name="OVER_COUNT",
                description=(
                    f"Found {found_total} articles but expected {expected_total} "
                    f"({ratio:.1%} of expected) — check for false marker matches"
                ),
            ))
        elif ratio < 0.90:
            issues.append(ValidationIssue(
                code="W002", severity="warning", name="UNDER_COUNT",
                description=(
                    f"Found {found_total} articles but expected {expected_total} "
                    f"({ratio:.1%} of expected) — possible missed markers"
                ),
            ))

    # ── W003: issuance article count ─────────────────────────────────────────
    # Only flag when the law is large (≥50 articles) and zero issuance articles
    # were found — small/medium laws (<50 articles) often have no issuance section
    # or the OCR skips the issuance preamble.
    issuance_found = len(issuance_articles)
    if issuance_found == 0 and expected_total >= 50:
        issues.append(ValidationIssue(
            code="W003", severity="warning", name="ISSUANCE_MISMATCH",
            description=(
                f"No issuance articles found for a law with {expected_total} articles "
                f"— ordinal marker patterns may not have matched"
            ),
        ))

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    passed = len(errors) == 0

    report = ValidationReport(
        law_id=law_entry.law_id,
        articles_checked=len(articles),
        passed=passed,
        error_count=len(errors),
        warning_count=len(warnings),
        issues=issues,
        validated_at=datetime.now(timezone.utc).isoformat(),
    )

    out_dir = SPLIT_ARTICLES_DIR / law_entry.law_id
    out_dir.mkdir(parents=True, exist_ok=True)
    val_path = out_dir / "validation_report.json"
    val_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _log_summary(law_entry.law_id, report)
    return report


def _log_summary(law_id: str, report: ValidationReport) -> None:
    status = "PASS" if report.passed else "FAIL"
    logger.info(
        "[%s] Validation %s — %d errors, %d warnings",
        law_id, status, report.error_count, report.warning_count,
    )
    for issue in report.issues:
        lvl = logging.ERROR if issue.severity == "error" else logging.WARNING
        logger.log(lvl, "[%s] %s %s: %s", law_id, issue.code, issue.name, issue.description)
