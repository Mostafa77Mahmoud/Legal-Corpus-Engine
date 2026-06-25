"""
Stage 6 — Assembly
===================
Input:  data/enriched_articles/{LAW_ID}/articles.json
        data/chunks/{LAW_ID}/chunks.json
Output: data/assembled/{LAW_ID}/articles_final.json
        data/assembled/{LAW_ID}/chunks_final.json
        data/assembled/{LAW_ID}/assembly_report.json

What this stage does:
  1. Load enriched articles + chunks.
  2. Enforce is_repealed from law_registry (authoritative source).
  3. Set is_current_version = True (one version per law in current corpus).
  4. Deduplicate by article_id (keeps last occurrence; duplicates logged).
  5. Sort articles by article_number, then issuance articles at end.
  6. Resolve explicit_cross_refs → add target_article_id for same-law refs.
  7. Inject corpus_metadata (schema_version, pipeline_version, law metadata).
  8. Produce clean final JSON files ready for Stage 7 export.

No Gemini calls — pure Python, zero API cost.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.law_registry import LawEntry
from config.settings import CHUNKS_DIR, ENRICHED_ARTICLES_DIR

logger = logging.getLogger(__name__)

ASSEMBLED_DIR     = ENRICHED_ARTICLES_DIR.parent / "assembled"
EXTRACTED_RAW_DIR = ENRICHED_ARTICLES_DIR.parent / "extracted_raw"

PIPELINE_VERSION = "1.1.0"
SCHEMA_VERSION   = "1.1"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class AssemblyReport:
    law_id: str
    law_name_ar: str
    total_articles_in: int
    total_articles_out: int
    total_chunks_in: int
    total_chunks_out: int
    duplicates_removed: int
    repealed_flagged: int
    assembled_at: str
    output_dir: str


# ── Public run function ───────────────────────────────────────────────────────

def run(law_entry: LawEntry) -> AssemblyReport:
    """
    Assemble the final corpus files for *law_entry*.
    Returns AssemblyReport.
    """
    law_id = law_entry.law_id
    repealed_set: frozenset[int] = frozenset(law_entry.repealed_articles)

    # ── Load enriched articles ────────────────────────────────────────────────
    articles_path = ENRICHED_ARTICLES_DIR / law_id / "articles.json"
    if not articles_path.exists():
        raise FileNotFoundError(
            f"Enriched articles not found: {articles_path}\nRun Stage 3 first."
        )
    articles_raw: list[dict[str, Any]] = json.loads(
        articles_path.read_text(encoding="utf-8")
    )

    # ── Load chunks ───────────────────────────────────────────────────────────
    chunks_path = CHUNKS_DIR / law_id / "chunks.json"
    if not chunks_path.exists():
        raise FileNotFoundError(
            f"Chunks not found: {chunks_path}\nRun Stage 3.7 first."
        )
    chunks_raw: list[dict[str, Any]] = json.loads(
        chunks_path.read_text(encoding="utf-8")
    )

    # ── Deduplicate articles by article_id (keep last) ────────────────────────
    seen_ids: dict[str, dict[str, Any]] = {}
    duplicates_removed = 0
    for art in articles_raw:
        aid = art.get("article_id", "")
        if aid in seen_ids:
            duplicates_removed += 1
            logger.warning("[%s] Duplicate article_id removed: %s", law_id, aid)
        seen_ids[aid] = art

    # ── Apply is_repealed + is_current_version ────────────────────────────────
    repealed_flagged = 0
    final_articles: list[dict[str, Any]] = []
    for art in seen_ids.values():
        art_num = art.get("article_number")
        should_be_repealed = (art_num in repealed_set) if art_num is not None else False

        if should_be_repealed and not art.get("is_repealed", False):
            repealed_flagged += 1

        art["is_repealed"]       = should_be_repealed
        art["is_current_version"] = True
        final_articles.append(art)

    # ── Sort: main articles by number, then issuance articles ─────────────────
    def sort_key(a: dict[str, Any]) -> tuple[int, int]:
        is_issuance = 1 if a.get("article_type") == "issuance" else 0
        return (is_issuance, a.get("article_number") or 0)

    final_articles.sort(key=sort_key)

    # ── Resolve explicit_cross_refs → target_article_id ───────────────────────
    # Build article_number → article_id lookup for this law
    num_to_id: dict[int, str] = {
        a["article_number"]: a["article_id"]
        for a in final_articles
        if a.get("article_number") is not None
    }
    cross_ref_resolved = 0
    for art in final_articles:
        refs = art.get("explicit_cross_refs")
        if not refs:
            continue
        resolved_refs = []
        for ref in refs:
            ref = dict(ref)  # always copy before mutating
            same_law = ref.get("same_law", True)
            if same_law:
                nums = ref.get("article_numbers", [])
                resolved_ids = [num_to_id[n] for n in nums if n in num_to_id]
                ref["target_article_ids"] = resolved_ids if resolved_ids else None
                cross_ref_resolved += len(resolved_ids)
            else:
                ref["target_article_ids"] = None  # cross-law ref — law_id unknown at this stage
            resolved_refs.append(ref)
        art["explicit_cross_refs"] = resolved_refs
    logger.info("[%s] Resolved %d same-law cross-ref target_article_ids", law_id, cross_ref_resolved)

    # ── Load confidence_score from Stage 1.5 report (if available) ────────────
    confidence_score: float | None = None
    conf_path = EXTRACTED_RAW_DIR / f"{law_id}_confidence.json"
    if conf_path.exists():
        try:
            conf_data = json.loads(conf_path.read_text(encoding="utf-8"))
            confidence_score = conf_data.get("confidence_score")
        except Exception:
            pass

    # ── Determine extraction_source from law_registry ─────────────────────────
    extraction_source = "gemini_ocr" if law_entry.txt_filename is None else "text_file"

    # ── Inject corpus_metadata into every article ─────────────────────────────
    for art in final_articles:
        art["corpus_metadata"] = {
            "schema_version":    SCHEMA_VERSION,
            "pipeline_version":  PIPELINE_VERSION,
            "extraction_source": extraction_source,
            "confidence_score":  confidence_score,
        }

    # ── Propagate is_repealed to chunks ───────────────────────────────────────
    repealed_article_ids: frozenset[str] = frozenset(
        a["article_id"] for a in final_articles if a.get("is_repealed")
    )
    final_chunks: list[dict[str, Any]] = []
    for chunk in chunks_raw:
        chunk["is_repealed"]       = chunk.get("article_id", "") in repealed_article_ids
        chunk["is_current_version"] = True
        final_chunks.append(chunk)

    # ── Write outputs ─────────────────────────────────────────────────────────
    out_dir = ASSEMBLED_DIR / law_id
    out_dir.mkdir(parents=True, exist_ok=True)

    articles_out_path = out_dir / "articles_final.json"
    articles_out_path.write_text(
        json.dumps(final_articles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    chunks_out_path = out_dir / "chunks_final.json"
    chunks_out_path.write_text(
        json.dumps(final_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assembled_at = datetime.now(timezone.utc).isoformat()
    report_data = {
        "law_id":              law_id,
        "law_name_ar":         law_entry.law_name_ar,
        "assembled_at":        assembled_at,
        "total_articles_in":   len(articles_raw),
        "total_articles_out":  len(final_articles),
        "total_chunks_in":     len(chunks_raw),
        "total_chunks_out":    len(final_chunks),
        "duplicates_removed":  duplicates_removed,
        "repealed_articles":   len(repealed_set),
        "repealed_flagged":    repealed_flagged,
        "is_current_version":  True,
    }
    (out_dir / "assembly_report.json").write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "[%s] Stage 6 done — %d articles → %d final, %d chunks → %d final",
        law_id, len(articles_raw), len(final_articles),
        len(chunks_raw), len(final_chunks),
    )

    return AssemblyReport(
        law_id=law_id,
        law_name_ar=law_entry.law_name_ar,
        total_articles_in=len(articles_raw),
        total_articles_out=len(final_articles),
        total_chunks_in=len(chunks_raw),
        total_chunks_out=len(final_chunks),
        duplicates_removed=duplicates_removed,
        repealed_flagged=repealed_flagged,
        assembled_at=assembled_at,
        output_dir=str(out_dir),
    )
