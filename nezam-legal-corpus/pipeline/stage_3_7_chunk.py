"""
Stage 3.7 — Article Chunking
==============================
Input:  data/enriched_articles/{LAW_ID}/articles.json
Output: data/chunks/{LAW_ID}/chunks.json
        data/chunks/{LAW_ID}/chunking_report.json

Splits each enriched article into semantically coherent chunks suitable
for vector embeddings.  Chunks respect paragraph and sentence boundaries.

Chunking rules
--------------
- Short articles (≤ CHUNK_WORD_LIMIT words)  → single chunk
- Long articles                               → split by double-newline paragraphs;
                                                if a paragraph still exceeds the limit,
                                                split by Arabic sentence boundary (. or ،)
- Adjacent chunks share an OVERLAP_WORDS overlap window (sliding window)
- Chunk IDs: {article_id}_C{index:03d}  e.g. EG_PDPL_008_C001
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.law_registry import LawEntry
from config.settings import CHUNKS_DIR, ENRICHED_ARTICLES_DIR

import logging
logger = logging.getLogger(__name__)

# ── Tuning parameters ─────────────────────────────────────────────────────────

CHUNK_WORD_LIMIT = 250    # target max words per chunk (≈300-400 Arabic tokens)
OVERLAP_WORDS    = 30     # words of overlap carried into the next chunk

# Sentence boundary pattern — splits on ". " or "، " followed by uppercase/Arabic
_SENT_RE = re.compile(r"(?<=[.،؟!])\s+")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    chunk_id: str
    article_id: str
    law_id: str
    chunk_index: int         # 0-based index within the article
    chunk_total: int         # total chunks for this article
    text: str
    word_count: int
    char_count: int
    has_overlap: bool        # True if this chunk carries overlap from previous
    # Inherited article metadata (denormalised for retrieval convenience)
    article_number: int
    article_type: str
    article_category: str
    topic: str
    keywords: list[str]      = field(default_factory=list)
    legal_entities: list[str] = field(default_factory=list)
    is_repealed: bool         = False


@dataclass
class ChunkingReport:
    law_id: str
    total_articles: int
    total_chunks: int
    single_chunk_articles: int
    multi_chunk_articles: int
    avg_chunk_words: float
    max_chunk_words: int
    min_chunk_words: int
    chunked_at: str


# ── Text splitting helpers ────────────────────────────────────────────────────

def _words(text: str) -> list[str]:
    return text.split()


def _split_paragraphs(text: str) -> list[str]:
    """Split on double (or more) newlines; strip each paragraph."""
    parts = re.split(r"\n{2,}", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _split_sentences(text: str) -> list[str]:
    """Split on Arabic sentence boundaries; strip each sentence."""
    parts = _SENT_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _merge_until_limit(segments: list[str], limit: int) -> list[str]:
    """
    Greedily merge consecutive segments into chunks of at most *limit* words.
    A segment that exceeds *limit* words on its own is kept as one oversized chunk
    (better than cutting mid-sentence).
    """
    chunks: list[str] = []
    current_words: list[str] = []

    for seg in segments:
        seg_words = _words(seg)
        if current_words and len(current_words) + len(seg_words) > limit:
            chunks.append(" ".join(current_words))
            current_words = seg_words
        else:
            current_words.extend(seg_words)

    if current_words:
        chunks.append(" ".join(current_words))

    return chunks


def _apply_overlap(raw_chunks: list[str], overlap: int) -> list[tuple[str, bool]]:
    """
    Prepend the last *overlap* words of chunk[i-1] to chunk[i].
    Returns list of (text, has_overlap) tuples.
    """
    result: list[tuple[str, bool]] = []
    for i, chunk in enumerate(raw_chunks):
        if i == 0 or overlap == 0:
            result.append((chunk, False))
        else:
            tail = _words(raw_chunks[i - 1])[-overlap:]
            result.append((" ".join(tail) + " " + chunk, True))
    return result


def _chunk_article(text: str) -> list[tuple[str, bool]]:
    """
    Return a list of (chunk_text, has_overlap) for one article's text.
    """
    word_count = len(_words(text))

    # Short article → single chunk, no overlap needed
    if word_count <= CHUNK_WORD_LIMIT:
        return [(text.strip(), False)]

    # Try paragraph split first
    paragraphs = _split_paragraphs(text)

    # Any paragraph that still exceeds limit → sentence split
    fine_segments: list[str] = []
    for para in paragraphs:
        if len(_words(para)) > CHUNK_WORD_LIMIT:
            fine_segments.extend(_split_sentences(para))
        else:
            fine_segments.append(para)

    # Merge segments into limit-sized chunks
    raw_chunks = _merge_until_limit(fine_segments, CHUNK_WORD_LIMIT)

    # Apply overlap window
    return _apply_overlap(raw_chunks, OVERLAP_WORDS)


# ── Public run function ───────────────────────────────────────────────────────

def run(law_entry: LawEntry) -> ChunkingReport:
    """
    Chunk all enriched articles for *law_entry*.

    Returns
    -------
    ChunkingReport
    """
    # ── Load enriched articles ────────────────────────────────────────────────
    in_path = ENRICHED_ARTICLES_DIR / law_entry.law_id / "articles.json"
    if not in_path.exists():
        raise FileNotFoundError(
            f"Enriched articles not found: {in_path}\n"
            f"Run Stage 3 first."
        )
    articles: list[dict[str, Any]] = json.loads(in_path.read_text(encoding="utf-8"))

    # ── Chunk each article ────────────────────────────────────────────────────
    out_dir = CHUNKS_DIR / law_entry.law_id
    out_dir.mkdir(parents=True, exist_ok=True)

    all_chunks: list[dict[str, Any]] = []
    single_chunk_count = 0
    multi_chunk_count  = 0
    all_word_counts: list[int] = []

    for article in articles:
        text = article.get("text", "").strip()
        if not text:
            logger.warning("[%s] Article %s has empty text — skipping", law_entry.law_id, article.get("article_id"))
            continue

        chunk_texts = _chunk_article(text)
        chunk_total = len(chunk_texts)

        if chunk_total == 1:
            single_chunk_count += 1
        else:
            multi_chunk_count += 1

        for idx, (chunk_text, has_overlap) in enumerate(chunk_texts):
            wc = len(_words(chunk_text))
            all_word_counts.append(wc)
            chunk_id = f"{article['article_id']}_C{idx + 1:03d}"

            chunk = Chunk(
                chunk_id=chunk_id,
                article_id=article["article_id"],
                law_id=law_entry.law_id,
                chunk_index=idx,
                chunk_total=chunk_total,
                text=chunk_text,
                word_count=wc,
                char_count=len(chunk_text),
                has_overlap=has_overlap,
                article_number=article.get("article_number", 0),
                article_type=article.get("article_type", ""),
                article_category=article.get("article_category", ""),
                topic=article.get("topic", ""),
                keywords=article.get("keywords", []),
                legal_entities=article.get("legal_entities", []),
                is_repealed=article.get("is_repealed", False),
            )
            all_chunks.append(asdict(chunk))

        logger.debug(
            "[%s] %s → %d chunk(s)",
            law_entry.law_id, article["article_id"], chunk_total,
        )

    # ── Write output ──────────────────────────────────────────────────────────
    out_path = out_dir / "chunks.json"
    out_path.write_text(
        json.dumps(all_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ── Build report ──────────────────────────────────────────────────────────
    total_chunks = len(all_chunks)
    avg_wc   = round(sum(all_word_counts) / total_chunks, 1) if total_chunks else 0.0
    max_wc   = max(all_word_counts) if all_word_counts else 0
    min_wc   = min(all_word_counts) if all_word_counts else 0

    report = ChunkingReport(
        law_id=law_entry.law_id,
        total_articles=len(articles),
        total_chunks=total_chunks,
        single_chunk_articles=single_chunk_count,
        multi_chunk_articles=multi_chunk_count,
        avg_chunk_words=avg_wc,
        max_chunk_words=max_wc,
        min_chunk_words=min_wc,
        chunked_at=datetime.now(timezone.utc).isoformat(),
    )
    report_path = out_dir / "chunking_report.json"
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "[%s] Stage 3.7 done — %d articles → %d chunks (single=%d multi=%d) avg_words=%.1f",
        law_entry.law_id, len(articles), total_chunks,
        single_chunk_count, multi_chunk_count, avg_wc,
    )
    return report
