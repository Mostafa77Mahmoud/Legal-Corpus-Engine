"""
Stage 7 — JSON Export (+ optional MongoDB)
==========================================
Input:  data/assembled/{LAW_ID}/articles_final.json
        data/assembled/{LAW_ID}/chunks_final.json
Output: data/releases/{LAW_ID}/
          articles.json          — pretty-printed JSON array
          articles.jsonl         — one article per line (for vector DB import)
          chunks.json            — pretty-printed JSON array
          chunks.jsonl           — one chunk per line (for vector DB import)
          release_metadata.json  — stats, schema version, processing dates

MongoDB export (optional):
  Set MONGODB_URI environment variable to enable.
  Collections: egyptian_law_articles, egyptian_law_chunks

No Gemini calls — pure Python, zero API cost.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.law_registry import LawEntry

logger = logging.getLogger(__name__)

ASSEMBLED_DIR = Path(__file__).parent.parent / "data" / "assembled"
RELEASES_DIR  = Path(__file__).parent.parent / "data" / "releases"

SCHEMA_VERSION = "1.0"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ExportReport:
    law_id: str
    law_name_ar: str
    total_articles: int
    total_chunks: int
    mongodb_exported: bool
    exported_at: str
    output_dir: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _export_to_mongodb(
    law_id: str,
    articles: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> bool:
    """
    Export to MongoDB if MONGODB_URI is set.
    Returns True if export succeeded, False otherwise.
    Uses upsert by article_id / chunk_id to be idempotent.
    """
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        logger.info("[%s] MONGODB_URI not set — skipping MongoDB export.", law_id)
        return False

    try:
        import pymongo  # type: ignore
    except ImportError:
        logger.warning("[%s] pymongo not installed — skipping MongoDB export.", law_id)
        return False

    try:
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=10_000)
        db = client["nezam_corpus"]

        # Upsert articles
        art_col = db["egyptian_law_articles"]
        for art in articles:
            art_col.update_one(
                {"article_id": art["article_id"]},
                {"$set": art},
                upsert=True,
            )

        # Upsert chunks
        chunk_col = db["egyptian_law_chunks"]
        for chunk in chunks:
            chunk_col.update_one(
                {"chunk_id": chunk["chunk_id"]},
                {"$set": chunk},
                upsert=True,
            )

        # Ensure indexes
        art_col.create_index(
            [("law_id", pymongo.ASCENDING), ("article_number", pymongo.ASCENDING)],
            unique=True, background=True,
        )
        chunk_col.create_index([("article_id", pymongo.ASCENDING)], background=True)
        chunk_col.create_index([("law_id", pymongo.ASCENDING)], background=True)

        client.close()
        logger.info(
            "[%s] MongoDB export done — %d articles, %d chunks upserted.",
            law_id, len(articles), len(chunks),
        )
        return True

    except Exception as exc:
        logger.warning("[%s] MongoDB export failed: %s", law_id, exc)
        return False


# ── Public run function ───────────────────────────────────────────────────────

def run(law_entry: LawEntry) -> ExportReport:
    """
    Export assembled corpus for *law_entry* to release files.
    Optionally exports to MongoDB if MONGODB_URI is set.
    Returns ExportReport.
    """
    law_id = law_entry.law_id

    # ── Load assembled files ──────────────────────────────────────────────────
    assembled_dir = ASSEMBLED_DIR / law_id
    articles_path = assembled_dir / "articles_final.json"
    chunks_path   = assembled_dir / "chunks_final.json"

    if not articles_path.exists():
        raise FileNotFoundError(
            f"Assembled articles not found: {articles_path}\nRun Stage 6 first."
        )
    if not chunks_path.exists():
        raise FileNotFoundError(
            f"Assembled chunks not found: {chunks_path}\nRun Stage 6 first."
        )

    articles: list[dict[str, Any]] = json.loads(articles_path.read_text(encoding="utf-8"))
    chunks:   list[dict[str, Any]] = json.loads(chunks_path.read_text(encoding="utf-8"))

    # ── Prepare output directory ──────────────────────────────────────────────
    out_dir = RELEASES_DIR / law_id
    out_dir.mkdir(parents=True, exist_ok=True)

    exported_at = datetime.now(timezone.utc).isoformat()

    # ── Write JSON files ──────────────────────────────────────────────────────
    _write_json(out_dir / "articles.json", articles)
    _write_jsonl(out_dir / "articles.jsonl", articles)
    _write_json(out_dir / "chunks.json", chunks)
    _write_jsonl(out_dir / "chunks.jsonl", chunks)

    logger.info(
        "[%s] Wrote %d articles and %d chunks to %s",
        law_id, len(articles), len(chunks), out_dir,
    )

    # ── Compute statistics ────────────────────────────────────────────────────
    categories: dict[str, int] = {}
    for art in articles:
        cat = art.get("article_category", "أخرى")
        categories[cat] = categories.get(cat, 0) + 1

    total_words = sum(a.get("word_count", 0) for a in articles)
    total_chunk_words = sum(c.get("word_count", 0) for c in chunks)
    repealed_count = sum(1 for a in articles if a.get("is_repealed"))
    main_count     = sum(1 for a in articles if a.get("article_type") == "main" and not a.get("is_repealed"))
    issuance_count = sum(1 for a in articles if a.get("article_type") == "issuance")

    # ── Enrichment source stats ───────────────────────────────────────────────
    models_used: dict[str, int] = {}
    for art in articles:
        m = art.get("enrichment_model", "unknown")
        models_used[str(m)] = models_used.get(str(m), 0) + 1

    # ── Write release metadata ────────────────────────────────────────────────
    metadata = {
        "schema_version":    SCHEMA_VERSION,
        "law_id":            law_id,
        "law_name_ar":       law_entry.law_name_ar,
        "law_number":        law_entry.law_number,
        "year":              law_entry.year,
        "exported_at":       exported_at,
        "is_current_version": True,
        "statistics": {
            "total_articles":    len(articles),
            "active_articles":   main_count,
            "issuance_articles": issuance_count,
            "repealed_articles": repealed_count,
            "total_chunks":      len(chunks),
            "total_article_words": total_words,
            "total_chunk_words":   total_chunk_words,
            "avg_words_per_article": round(total_words / len(articles), 1) if articles else 0,
            "avg_words_per_chunk":   round(total_chunk_words / len(chunks), 1) if chunks else 0,
            "article_categories": categories,
        },
        "enrichment": {
            "models_used": models_used,
        },
        "output_files": {
            "articles_json":  "articles.json",
            "articles_jsonl": "articles.jsonl",
            "chunks_json":    "chunks.json",
            "chunks_jsonl":   "chunks.jsonl",
        },
    }
    _write_json(out_dir / "release_metadata.json", metadata)
    logger.info("[%s] Wrote release_metadata.json", law_id)

    # ── Optional MongoDB export ───────────────────────────────────────────────
    mongodb_exported = _export_to_mongodb(law_id, articles, chunks)

    return ExportReport(
        law_id=law_id,
        law_name_ar=law_entry.law_name_ar,
        total_articles=len(articles),
        total_chunks=len(chunks),
        mongodb_exported=mongodb_exported,
        exported_at=exported_at,
        output_dir=str(out_dir),
    )
