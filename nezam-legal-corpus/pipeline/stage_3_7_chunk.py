"""
Stage 3.7 — Article Chunking  (Rule-based + Optional Semantic Mode)
=====================================================================
Input:  data/enriched_articles/{LAW_ID}/articles.json
Output: data/chunks/{LAW_ID}/chunks.json
        data/chunks/{LAW_ID}/chunking_report.json

وضعان للتقسيم
--------------
1. Rule-based (افتراضي، مجاني):
   - مواد قصيرة (≤ CHUNK_WORD_LIMIT كلمة) → chunk واحد
   - مواد طويلة → تقسيم على حدود الفقرات، ثم الجمل إذا لزم
   - نافذة overlap (30 كلمة) بين الـ chunks

2. Semantic (اختياري، يستخدم Gemini):
   فعّله بـ SEMANTIC_CHUNKING=true أو use_semantic_chunking=True في run()
   - المواد القصيرة: rule-based بدون تغيير (لا Gemini call)
   - المواد الطويلة فقط: Gemini يحدد حدود التقسيم الدلالية
   - المزايا: كل chunk يعالج فكرة قانونية مكتملة، مفهوم بشكل مستقل
   - التكلفة: call واحد لكل مادة طويلة (عادة 4-10% من المواد)

Chunk IDs: {article_id}_C{index:03d}   مثال: EG_PDPL_008_C001
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.law_registry import LawEntry
from config.settings import CHUNKS_DIR, ENRICHED_ARTICLES_DIR, SEMANTIC_CHUNKING

import logging
logger = logging.getLogger(__name__)


# ── Tuning parameters ─────────────────────────────────────────────────────────

CHUNK_WORD_LIMIT = 250    # حد الكلمات للـ chunk الواحد (~300-400 token عربي)
OVERLAP_WORDS    = 30     # كلمات الـ overlap بين الـ chunks المتجاورة

_SENT_RE = re.compile(r"(?<=[.،؟!])\s+")


# ── Semantic chunking prompt ──────────────────────────────────────────────────

_SEMANTIC_CHUNK_PROMPT = """\
أنت متخصص في تحليل النصوص القانونية المصرية.

المادة التالية ({article_id}) من قانون {law_name} تحتاج إلى تقسيم.

نص المادة:
{text}

مهمتك: قسّم هذا النص إلى أجزاء (chunks) متماسكة دلالياً بحيث:
- كل chunk يعالج فكرة قانونية واحدة مكتملة
- كل chunk مفهوم بشكل مستقل دون الحاجة للأجزاء الأخرى
- لا تحذف أي كلمة من النص — قسّم فقط، لا تعدّل أو تلخص
- الحد الأقصى: {word_limit} كلمة للـ chunk الواحد
- الحد الأدنى: 30 كلمة للـ chunk الواحد

أعد JSON array من النصوص فقط — بدون أي كلام خارج الـ JSON:
["نص الجزء الأول كاملاً...", "نص الجزء الثاني كاملاً...", ...]

تحقق: مجموع كلمات كل الأجزاء يجب أن يساوي تقريباً عدد كلمات النص الأصلي.
"""


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    chunk_id: str
    article_id: str
    law_id: str
    chunk_index: int
    chunk_total: int
    text: str
    word_count: int
    char_count: int
    has_overlap: bool
    chunk_method: str           # "rule_based" | "semantic"
    article_number: int
    article_type: str
    article_category: str
    topic: str
    keywords: list[str]        = field(default_factory=list)
    legal_entities: list[str]  = field(default_factory=list)
    is_repealed: bool          = False


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
    semantic_chunks: int        # chunks مُقسَّمة بـ Gemini
    rule_based_chunks: int      # chunks مُقسَّمة بالـ rule-based
    chunked_at: str


# ── Text helpers (rule-based) ─────────────────────────────────────────────────

def _words(text: str) -> list[str]:
    return text.split()


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n{2,}", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _merge_until_limit(segments: list[str], limit: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for seg in segments:
        seg_words = _words(seg)
        if current and len(current) + len(seg_words) > limit:
            chunks.append(" ".join(current))
            current = seg_words
        else:
            current.extend(seg_words)
    if current:
        chunks.append(" ".join(current))
    return chunks


def _apply_overlap(raw_chunks: list[str], overlap: int) -> list[tuple[str, bool]]:
    result: list[tuple[str, bool]] = []
    for i, chunk in enumerate(raw_chunks):
        if i == 0 or overlap == 0:
            result.append((chunk, False))
        else:
            tail = _words(raw_chunks[i - 1])[-overlap:]
            result.append((" ".join(tail) + " " + chunk, True))
    return result


def _rule_chunk_article(text: str) -> list[tuple[str, bool]]:
    """
    Rule-based chunking.
    Returns list of (chunk_text, has_overlap).
    """
    if len(_words(text)) <= CHUNK_WORD_LIMIT:
        return [(text.strip(), False)]

    paragraphs = _split_paragraphs(text)
    fine_segments: list[str] = []
    for para in paragraphs:
        if len(_words(para)) > CHUNK_WORD_LIMIT:
            fine_segments.extend(_split_sentences(para))
        else:
            fine_segments.append(para)

    raw_chunks = _merge_until_limit(fine_segments, CHUNK_WORD_LIMIT)
    return _apply_overlap(raw_chunks, OVERLAP_WORDS)


# ── Semantic chunking (Gemini) ────────────────────────────────────────────────

def _semantic_chunk_article(
    text: str,
    article_id: str,
    law_name: str,
    cost_tracker: Any,
    model: str,
) -> list[tuple[str, bool]] | None:
    """
    Use Gemini to split the article text at semantic boundaries.

    Returns list of (chunk_text, has_overlap=False), or None if Gemini call
    fails (caller should fall back to rule-based).
    """
    try:
        from utils.llm_client import generate_text

        prompt = _SEMANTIC_CHUNK_PROMPT.format(
            article_id=article_id,
            law_name=law_name,
            text=text.strip(),
            word_limit=CHUNK_WORD_LIMIT,
        )
        raw = generate_text(
            prompt=prompt,
            cost_tracker=cost_tracker,
            stage="stage_3_7_semantic",
            law_id=article_id.rsplit("_", 1)[0],
            model_name=model,
        )

        # Parse JSON array
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
        raw = raw.strip()

        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON array found in response")

        chunks_raw: list[str] = json.loads(raw[start:end])

        if not chunks_raw or not all(isinstance(c, str) for c in chunks_raw):
            raise ValueError("Invalid chunk array format")

        # Validate: total words should be close to original (~10% tolerance)
        original_wc = len(_words(text))
        returned_wc = sum(len(_words(c)) for c in chunks_raw)
        if returned_wc < original_wc * 0.85:
            logger.warning(
                "[%s] Semantic chunk word count mismatch: original=%d returned=%d — falling back",
                article_id, original_wc, returned_wc,
            )
            return None

        # Semantic chunks have no overlap (boundaries are semantically natural)
        return [(c.strip(), False) for c in chunks_raw if c.strip()]

    except Exception as exc:
        logger.warning(
            "[%s] Semantic chunking failed (%s) — falling back to rule-based",
            article_id, exc,
        )
        return None


# ── Public run function ───────────────────────────────────────────────────────

def run(
    law_entry: LawEntry,
    use_semantic_chunking: bool = SEMANTIC_CHUNKING,
    cost_tracker: Any = None,
    model: str | None = None,
) -> ChunkingReport:
    """
    Chunk all enriched articles for *law_entry*.

    Parameters
    ----------
    use_semantic_chunking : bool
        If True, long articles are chunked by Gemini (semantic boundaries)
        instead of the rule-based paragraph/sentence splitter.
        Short articles (≤ CHUNK_WORD_LIMIT words) always use rule-based.
    cost_tracker : CostTracker | None
        Required when use_semantic_chunking=True.
    model : str | None
        Gemini model name. Defaults to PRIMARY_MODEL from settings.
    """
    if use_semantic_chunking and cost_tracker is None:
        raise ValueError("cost_tracker is required when use_semantic_chunking=True")

    if model is None:
        from config.settings import PRIMARY_MODEL
        model = PRIMARY_MODEL

    # ── Load enriched articles ────────────────────────────────────────────────
    in_path = ENRICHED_ARTICLES_DIR / law_entry.law_id / "articles.json"
    if not in_path.exists():
        raise FileNotFoundError(
            f"Enriched articles not found: {in_path}\nRun Stage 3 first."
        )
    articles: list[dict[str, Any]] = json.loads(in_path.read_text(encoding="utf-8"))

    out_dir = CHUNKS_DIR / law_entry.law_id
    out_dir.mkdir(parents=True, exist_ok=True)

    all_chunks: list[dict[str, Any]] = []
    single_chunk_count = 0
    multi_chunk_count  = 0
    semantic_chunk_count = 0
    rule_based_chunk_count = 0
    all_word_counts: list[int] = []

    mode_label = "semantic+rule-based" if use_semantic_chunking else "rule-based"
    logger.info("[%s] Stage 3.7 chunking mode: %s", law_entry.law_id, mode_label)

    for article in articles:
        text = article.get("text", "").strip()
        if not text:
            logger.warning(
                "[%s] Article %s has empty text — skipping",
                law_entry.law_id, article.get("article_id"),
            )
            continue

        article_id = article["article_id"]
        word_count = len(_words(text))
        chunk_method = "rule_based"
        chunk_texts: list[tuple[str, bool]] = []

        # ── Short article: always rule-based (no Gemini call needed) ─────────
        if word_count <= CHUNK_WORD_LIMIT:
            chunk_texts = [(text.strip(), False)]

        # ── Long article: try semantic if enabled ─────────────────────────────
        elif use_semantic_chunking:
            semantic_result = _semantic_chunk_article(
                text=text,
                article_id=article_id,
                law_name=law_entry.law_name_ar,
                cost_tracker=cost_tracker,
                model=model,
            )
            if semantic_result is not None:
                chunk_texts = semantic_result
                chunk_method = "semantic"
            else:
                chunk_texts = _rule_chunk_article(text)

        # ── Long article: rule-based only ─────────────────────────────────────
        else:
            chunk_texts = _rule_chunk_article(text)

        chunk_total = len(chunk_texts)
        if chunk_total == 1:
            single_chunk_count += 1
        else:
            multi_chunk_count += 1

        for idx, (chunk_text, has_overlap) in enumerate(chunk_texts):
            wc = len(_words(chunk_text))
            all_word_counts.append(wc)

            if chunk_method == "semantic":
                semantic_chunk_count += 1
            else:
                rule_based_chunk_count += 1

            chunk = Chunk(
                chunk_id=f"{article_id}_C{idx + 1:03d}",
                article_id=article_id,
                law_id=law_entry.law_id,
                chunk_index=idx,
                chunk_total=chunk_total,
                text=chunk_text,
                word_count=wc,
                char_count=len(chunk_text),
                has_overlap=has_overlap,
                chunk_method=chunk_method,
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
            "[%s] %s → %d chunk(s) [%s]",
            law_entry.law_id, article_id, chunk_total, chunk_method,
        )

    # ── Write chunks ──────────────────────────────────────────────────────────
    (out_dir / "chunks.json").write_text(
        json.dumps(all_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total_chunks = len(all_chunks)
    avg_wc = round(sum(all_word_counts) / total_chunks, 1) if total_chunks else 0.0
    report = ChunkingReport(
        law_id=law_entry.law_id,
        total_articles=len(articles),
        total_chunks=total_chunks,
        single_chunk_articles=single_chunk_count,
        multi_chunk_articles=multi_chunk_count,
        avg_chunk_words=avg_wc,
        max_chunk_words=max(all_word_counts) if all_word_counts else 0,
        min_chunk_words=min(all_word_counts) if all_word_counts else 0,
        semantic_chunks=semantic_chunk_count,
        rule_based_chunks=rule_based_chunk_count,
        chunked_at=datetime.now(timezone.utc).isoformat(),
    )
    (out_dir / "chunking_report.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "[%s] Stage 3.7 done — %d articles → %d chunks | rule-based=%d semantic=%d | avg=%.1f words",
        law_entry.law_id, len(articles), total_chunks,
        rule_based_chunk_count, semantic_chunk_count, avg_wc,
    )
    return report
