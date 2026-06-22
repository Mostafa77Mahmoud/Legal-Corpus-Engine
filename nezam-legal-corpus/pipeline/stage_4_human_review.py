"""
Stage 4 — Human Review Export
==============================
Input:  data/enriched_articles/{LAW_ID}/articles.json
        data/chunks/{LAW_ID}/chunks.json
Output: data/human_review/{LAW_ID}/articles_review.json
        data/human_review/{LAW_ID}/articles_review.csv
        data/human_review/{LAW_ID}/chunks_review.json
        data/human_review/{LAW_ID}/chunks_review.csv
        data/human_review/{LAW_ID}/review_manifest.json

Produces reviewer-ready files for manual QA of enrichment metadata and
chunks before the final corpus export.  Each record includes empty
`review_status` and `review_notes` fields that the team fills in:

    review_status : "approved" | "needs_edit" | "rejected" | ""
    review_notes  : free-text string

The manifest gives a high-level summary of the export and a checklist
of fields the reviewer should inspect.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.law_registry import LawEntry
from config.settings import CHUNKS_DIR, ENRICHED_ARTICLES_DIR, HUMAN_REVIEW_DIR

import logging
logger = logging.getLogger(__name__)


# ── Review field defaults ─────────────────────────────────────────────────────

_REVIEW_FIELDS: dict[str, str] = {
    "review_status": "",   # approved | needs_edit | rejected
    "review_notes":  "",   # free-text
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ReviewReport:
    law_id: str
    law_name_ar: str
    total_articles: int
    total_chunks: int
    issuance_articles: int
    main_articles: int
    enrichment_errors: int
    multi_chunk_articles: int
    exported_at: str
    output_dir: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _keywords_str(keywords: list[str]) -> str:
    """Join keywords list into a pipe-separated string for CSV."""
    return " | ".join(keywords) if keywords else ""


def _write_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _build_article_row(article: dict[str, Any], chunk_count: int) -> dict[str, Any]:
    """Build a reviewer-ready dict for one article."""
    row = {
        "article_id":         article.get("article_id", ""),
        "law_id":             article.get("law_id", ""),
        "article_number":     article.get("article_number", ""),
        "article_number_raw": article.get("article_number_raw", ""),
        "article_type":       article.get("article_type", ""),
        "article_category":   article.get("article_category", ""),
        "topic":              article.get("topic", ""),
        "keywords":           _keywords_str(article.get("keywords", [])),
        "legal_entities":     _keywords_str(article.get("legal_entities", [])),
        "article_summary":    article.get("article_summary", ""),
        "is_repealed":        article.get("is_repealed", False),
        "word_count":         article.get("word_count", 0),
        "char_count":         article.get("char_count", 0),
        "chunk_count":        chunk_count,
        "enrichment_error":   article.get("enrichment_error") or "",
        "text":               article.get("text", ""),
        **_REVIEW_FIELDS,
    }
    return row


def _build_chunk_row(chunk: dict[str, Any]) -> dict[str, Any]:
    """Build a reviewer-ready dict for one chunk."""
    row = {
        "chunk_id":         chunk.get("chunk_id", ""),
        "article_id":       chunk.get("article_id", ""),
        "law_id":           chunk.get("law_id", ""),
        "chunk_index":      chunk.get("chunk_index", 0),
        "chunk_total":      chunk.get("chunk_total", 1),
        "has_overlap":      chunk.get("has_overlap", False),
        "article_number":   chunk.get("article_number", ""),
        "article_type":     chunk.get("article_type", ""),
        "article_category": chunk.get("article_category", ""),
        "topic":            chunk.get("topic", ""),
        "keywords":         _keywords_str(chunk.get("keywords", [])),
        "legal_entities":   _keywords_str(chunk.get("legal_entities", [])),
        "is_repealed":      chunk.get("is_repealed", False),
        "word_count":       chunk.get("word_count", 0),
        "char_count":       chunk.get("char_count", 0),
        "text":             chunk.get("text", ""),
        **_REVIEW_FIELDS,
    }
    return row


# ── Public run function ───────────────────────────────────────────────────────

def run(law_entry: LawEntry) -> ReviewReport:
    """
    Export human-review files for *law_entry*.

    Returns
    -------
    ReviewReport
    """
    # ── Load enriched articles ────────────────────────────────────────────────
    articles_path = ENRICHED_ARTICLES_DIR / law_entry.law_id / "articles.json"
    if not articles_path.exists():
        raise FileNotFoundError(
            f"Enriched articles not found: {articles_path}\n"
            f"Run Stage 3 first."
        )
    articles: list[dict[str, Any]] = json.loads(
        articles_path.read_text(encoding="utf-8")
    )

    # ── Load chunks ───────────────────────────────────────────────────────────
    chunks_path = CHUNKS_DIR / law_entry.law_id / "chunks.json"
    if not chunks_path.exists():
        raise FileNotFoundError(
            f"Chunks not found: {chunks_path}\n"
            f"Run Stage 3.7 first."
        )
    chunks: list[dict[str, Any]] = json.loads(
        chunks_path.read_text(encoding="utf-8")
    )

    # ── Build chunk-count lookup per article ──────────────────────────────────
    chunk_count_by_article: dict[str, int] = {}
    for chunk in chunks:
        aid = chunk.get("article_id", "")
        chunk_count_by_article[aid] = chunk_count_by_article.get(aid, 0) + 1

    # ── Prepare output directory ──────────────────────────────────────────────
    out_dir = HUMAN_REVIEW_DIR / law_entry.law_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Build article review rows ─────────────────────────────────────────────
    article_rows: list[dict[str, Any]] = []
    issuance_count = 0
    main_count = 0
    enrichment_errors = 0
    multi_chunk_count = 0

    for art in articles:
        aid = art.get("article_id", "")
        ccount = chunk_count_by_article.get(aid, 0)
        row = _build_article_row(art, chunk_count=ccount)
        article_rows.append(row)

        if art.get("article_type") == "issuance":
            issuance_count += 1
        else:
            main_count += 1

        if art.get("enrichment_error"):
            enrichment_errors += 1

        if ccount > 1:
            multi_chunk_count += 1

    # ── Build chunk review rows ───────────────────────────────────────────────
    chunk_rows: list[dict[str, Any]] = [_build_chunk_row(c) for c in chunks]

    # ── Write articles JSON ───────────────────────────────────────────────────
    articles_json_path = out_dir / "articles_review.json"
    _write_json(articles_json_path, article_rows)
    logger.info("[%s] Wrote %d article rows → %s", law_entry.law_id, len(article_rows), articles_json_path)

    # ── Write articles CSV ────────────────────────────────────────────────────
    article_csv_fields = [
        "article_id", "law_id", "article_number", "article_number_raw",
        "article_type", "article_category", "topic", "keywords",
        "legal_entities", "article_summary", "is_repealed",
        "word_count", "char_count", "chunk_count", "enrichment_error",
        "review_status", "review_notes", "text",
    ]
    articles_csv_path = out_dir / "articles_review.csv"
    _write_csv(articles_csv_path, article_rows, fieldnames=article_csv_fields)
    logger.info("[%s] Wrote articles CSV → %s", law_entry.law_id, articles_csv_path)

    # ── Write chunks JSON ─────────────────────────────────────────────────────
    chunks_json_path = out_dir / "chunks_review.json"
    _write_json(chunks_json_path, chunk_rows)
    logger.info("[%s] Wrote %d chunk rows → %s", law_entry.law_id, len(chunk_rows), chunks_json_path)

    # ── Write chunks CSV ──────────────────────────────────────────────────────
    chunk_csv_fields = [
        "chunk_id", "article_id", "law_id", "chunk_index", "chunk_total",
        "has_overlap", "article_number", "article_type", "article_category",
        "topic", "keywords", "legal_entities", "is_repealed",
        "word_count", "char_count", "review_status", "review_notes", "text",
    ]
    chunks_csv_path = out_dir / "chunks_review.csv"
    _write_csv(chunks_csv_path, chunk_rows, fieldnames=chunk_csv_fields)
    logger.info("[%s] Wrote chunks CSV → %s", law_entry.law_id, chunks_csv_path)

    # ── Build and write manifest ──────────────────────────────────────────────
    exported_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "law_id":              law_entry.law_id,
        "law_name_ar":         law_entry.law_name_ar,
        "law_number":          law_entry.law_number,
        "year":                law_entry.year,
        "exported_at":         exported_at,
        "statistics": {
            "total_articles":       len(articles),
            "issuance_articles":    issuance_count,
            "main_articles":        main_count,
            "enrichment_errors":    enrichment_errors,
            "total_chunks":         len(chunks),
            "multi_chunk_articles": multi_chunk_count,
            "single_chunk_articles": len(articles) - multi_chunk_count,
        },
        "output_files": {
            "articles_json": "articles_review.json",
            "articles_csv":  "articles_review.csv",
            "chunks_json":   "chunks_review.json",
            "chunks_csv":    "chunks_review.csv",
        },
        "review_instructions": {
            "review_status_values": ["approved", "needs_edit", "rejected"],
            "fields_to_check": [
                "topic        — موضوع المادة الرئيسي، يجب أن يعكس محتوى المادة بدقة",
                "keywords     — الكلمات المفتاحية، تحقق من اكتمالها وعدم تكرارها",
                "article_category — التصنيف القانوني، يجب أن يتطابق مع محتوى الفصل",
                "article_summary — الملخص، يجب أن يكون دقيقاً وغير مضلل",
                "legal_entities  — الجهات القانونية المذكورة، تحقق من اكتمالها",
                "chunk text    — تأكد من أن كل chunk مفهوم بشكل مستقل",
            ],
        },
    }
    manifest_path = out_dir / "review_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[%s] Wrote manifest → %s", law_entry.law_id, manifest_path)

    report = ReviewReport(
        law_id=law_entry.law_id,
        law_name_ar=law_entry.law_name_ar,
        total_articles=len(articles),
        total_chunks=len(chunks),
        issuance_articles=issuance_count,
        main_articles=main_count,
        enrichment_errors=enrichment_errors,
        multi_chunk_articles=multi_chunk_count,
        exported_at=exported_at,
        output_dir=str(out_dir),
    )

    logger.info(
        "[%s] Stage 4 done — %d articles, %d chunks exported to %s",
        law_entry.law_id, len(articles), len(chunks), out_dir,
    )
    return report
