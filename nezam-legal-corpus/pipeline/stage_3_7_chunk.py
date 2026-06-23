"""
Stage 3.7 — Article Chunking  (Rule-based + Optional Semantic Mode)
=====================================================================
Input:  data/enriched_articles/{LAW_ID}/articles.json
Output: data/chunks/{LAW_ID}/chunks.json
        data/chunks/{LAW_ID}/chunking_report.json

التحسينات المطبّقة (بناءً على Google AI documentation):
══════════════════════════════════════════════════════════
1. Structured Output للـ Semantic Chunking:
   - response_schema = {"type": "array", "items": {"type": "string"}}
   - Gemini يعيد JSON array مضموناً — لا تحليل أو regex
   - يمنع مشكلة الـ partial JSON أو الـ formatting الخاطئ

2. System Instruction محسّن:
   - يعرّف المهمة بدقة: تقسيم قانوني لا تلخيص
   - مثال عملي لحدود التقسيم الصحيحة
   - تحذير من الحذف (يجب أن يبقى النص كاملاً)

3. temperature = 0.1 للـ semantic chunking:
   - أعلى قليلاً من 0.0 لأن الـ boundary detection يحتاج حكماً
   - يمنع الـ greedy decoding من التقسيم الميكانيكي
   - ما زال منخفضاً جداً لضمان الدقة القانونية

4. max_output_tokens = 32768 للـ semantic chunking:
   - المادة الطويلة (660 كلمة) ≈ 2000 token output عند إعادة النص كاملاً
   - 32768 يكفي لأي مادة قانونية طويلة

وضعان للتقسيم
--------------
1. Rule-based (افتراضي، مجاني):
   مواد قصيرة (≤ CHUNK_WORD_LIMIT) → chunk واحد
   مواد طويلة → فقرات → جمل → merge حتى الحد

2. Semantic (SEMANTIC_CHUNKING=true):
   المواد القصيرة: rule-based دائماً (صفر تكلفة)
   المواد الطويلة: Gemini يحدد الحدود الدلالية

Chunk IDs: {article_id}_C{index:03d}  مثال: EG_PDPL_008_C001
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from config.law_registry import LawEntry
from config.settings import (
    CHUNK_THINKING_BUDGET,
    CHUNK_THINKING_LEVEL,
    CHUNKS_DIR,
    ENRICHED_ARTICLES_DIR,
    GEMINI_MAX_OUTPUT_TOKENS,
    SEMANTIC_CHUNKING,
)

import logging
logger = logging.getLogger(__name__)


# ── Tuning parameters ─────────────────────────────────────────────────────────

CHUNK_WORD_LIMIT = 250    # حد الكلمات للـ chunk (~300-400 token عربي)
OVERLAP_WORDS    = 30     # overlap بين الـ chunks المتجاورة (rule-based فقط)

_SENT_RE = re.compile(r"(?<=[.،؟!])\s+")


# ── Structured Output Schema (Semantic Chunking) ──────────────────────────────
# مصدر: https://ai.google.dev/gemini-api/docs/structured-output

_SEMANTIC_CHUNKS_SCHEMA: dict = {
    "type": "array",
    "items": {
        "type": "string",
        "description": "نص chunk كامل من المادة القانونية",
    },
    "description": "المادة القانونية مقسّمة إلى أجزاء دلالية",
}


# ── System Instruction للـ Semantic Chunking ──────────────────────────────────

_SEMANTIC_SYSTEM_INSTRUCTION = """\
أنت محلل قانوني متخصص في التشريع المصري.
مهمتك: تقسيم نصوص المواد القانونية إلى أجزاء دلالية متماسكة للاستخدام في أنظمة RAG.

## مبادئ التقسيم القانوني الصحيح

الهدف: كل جزء (chunk) يجب أن يكون:
✓ مكتفياً بذاته — مفهوم دون قراءة الأجزاء الأخرى
✓ متماسكاً دلالياً — يعالج فكرة قانونية واحدة
✓ كاملاً لفظياً — لا يُحذف أي حرف من النص الأصلي

## حدود التقسيم المناسبة

انقسم عند:
- انتقال من تعريف إلى حكم تطبيقي
- انتقال من حقوق إلى التزامات
- انتقال من شرط عام إلى استثناء
- انتقال من بيان النطاق إلى الإجراء

لا تنقسم عند:
- منتصف الجملة
- منتصف القائمة (أ، ب، ج)
- منتصف الشرط وجوابه

## قاعدة صارمة
مجموع كلمات الأجزاء = مجموع كلمات النص الأصلي تماماً.
لا تُلخص ولا تُضف ولا تحذف.\
"""


# ── Semantic chunking prompt ──────────────────────────────────────────────────

_SEMANTIC_CHUNK_PROMPT = """\
<article id="{article_id}" law="{law_name}" words="{word_count}">
{text}
</article>

قسّم نص هذه المادة إلى أجزاء دلالية. الحد الأقصى لكل جزء: {word_limit} كلمة.\
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
    semantic_chunks: int
    rule_based_chunks: int
    chunked_at: str


# ── Rule-based helpers ────────────────────────────────────────────────────────

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
    """Rule-based chunking. Returns list of (chunk_text, has_overlap)."""
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


# ── Semantic chunking (Gemini + Structured Output) ────────────────────────────

def _semantic_chunk_article(
    text: str,
    article_id: str,
    law_name: str,
    cost_tracker: Any,
    model: str,
) -> list[tuple[str, bool]] | None:
    """
    Use Gemini to identify semantically meaningful split points.

    Returns list of (chunk_text, has_overlap=False), or None on failure
    (caller falls back to rule-based).

    Uses structured output → guaranteed JSON array of strings.
    temperature=0.1 (slight flexibility for boundary judgments).
    """
    try:
        from utils.llm_client import generate_text

        word_count = len(_words(text))
        prompt = _SEMANTIC_CHUNK_PROMPT.format(
            article_id=article_id,
            law_name=law_name,
            word_count=word_count,
            text=text.strip(),
            word_limit=CHUNK_WORD_LIMIT,
        )

        raw = generate_text(
            prompt=prompt,
            cost_tracker=cost_tracker,
            stage="stage_3_7_semantic",
            law_id=article_id.rsplit("_", 2)[0] if "_" in article_id else article_id,
            model_name=model,
            temperature=0.1,                        # مرونة طفيفة لقرارات الحدود
            max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,  # 65536 — full breathing room
            thinking_budget=CHUNK_THINKING_BUDGET,   # None = model auto-decides
            thinking_level=CHUNK_THINKING_LEVEL,     # e.g. "LOW" for gemini-3.x
            response_schema=_SEMANTIC_CHUNKS_SCHEMA,
            system_instruction=_SEMANTIC_SYSTEM_INSTRUCTION,
        )

        # JSON مضمون من response_schema
        chunks_raw: list[str] = json.loads(raw)

        if not chunks_raw or not all(isinstance(c, str) for c in chunks_raw):
            raise ValueError("Response is not a non-empty array of strings")

        # التحقق: مجموع الكلمات يجب أن يكون قريباً من الأصل (±15%)
        returned_wc = sum(len(_words(c)) for c in chunks_raw)
        if returned_wc < word_count * 0.85:
            logger.warning(
                "[%s] Semantic word count mismatch: original=%d returned=%d (%.0f%%) — fallback",
                article_id, word_count, returned_wc, returned_wc / word_count * 100,
            )
            return None

        clean = [(c.strip(), False) for c in chunks_raw if c.strip()]
        logger.info(
            "[%s] Semantic chunking: %d words → %d chunks",
            article_id, word_count, len(clean),
        )
        return clean

    except Exception as exc:
        logger.warning("[%s] Semantic chunking failed (%s) — rule-based fallback", article_id, exc)
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
        True → Gemini semantic chunking for long articles.
        False (default) → pure rule-based, free, no API calls.
    cost_tracker
        Required when use_semantic_chunking=True.
    model : str | None
        Gemini model. Defaults to PRIMARY_MODEL.
    """
    if use_semantic_chunking and cost_tracker is None:
        raise ValueError("cost_tracker is required when use_semantic_chunking=True")

    if model is None:
        from config.settings import PRIMARY_MODEL
        model = PRIMARY_MODEL

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
    semantic_count     = 0
    rule_based_count   = 0
    all_word_counts: list[int] = []

    mode_label = "semantic+rule-based" if use_semantic_chunking else "rule-based"
    logger.info("[%s] Stage 3.7 mode: %s", law_entry.law_id, mode_label)

    for article in articles:
        text = article.get("text", "").strip()
        if not text:
            logger.warning("[%s] Article %s has empty text — skipped", law_entry.law_id, article.get("article_id"))
            continue

        article_id  = article["article_id"]
        word_count  = len(_words(text))
        chunk_texts: list[tuple[str, bool]] = []
        chunk_method = "rule_based"

        # ── Short article → always rule-based (no API cost) ───────────────────
        if word_count <= CHUNK_WORD_LIMIT:
            chunk_texts = [(text.strip(), False)]

        # ── Long article → try semantic if enabled ────────────────────────────
        elif use_semantic_chunking:
            result = _semantic_chunk_article(
                text=text,
                article_id=article_id,
                law_name=law_entry.law_name_ar,
                cost_tracker=cost_tracker,
                model=model,
            )
            if result is not None:
                chunk_texts = result
                chunk_method = "semantic"
            else:
                chunk_texts = _rule_chunk_article(text)

        # ── Long article → rule-based ─────────────────────────────────────────
        else:
            chunk_texts = _rule_chunk_article(text)

        chunk_total = len(chunk_texts)
        single_chunk_count += (chunk_total == 1)
        multi_chunk_count  += (chunk_total > 1)

        for idx, (chunk_text, has_overlap) in enumerate(chunk_texts):
            wc = len(_words(chunk_text))
            all_word_counts.append(wc)

            if chunk_method == "semantic":
                semantic_count += 1
            else:
                rule_based_count += 1

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

    (out_dir / "chunks.json").write_text(
        json.dumps(all_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total = len(all_chunks)
    avg_wc = round(sum(all_word_counts) / total, 1) if total else 0.0
    report = ChunkingReport(
        law_id=law_entry.law_id,
        total_articles=len(articles),
        total_chunks=total,
        single_chunk_articles=single_chunk_count,
        multi_chunk_articles=multi_chunk_count,
        avg_chunk_words=avg_wc,
        max_chunk_words=max(all_word_counts) if all_word_counts else 0,
        min_chunk_words=min(all_word_counts) if all_word_counts else 0,
        semantic_chunks=semantic_count,
        rule_based_chunks=rule_based_count,
        chunked_at=datetime.now(timezone.utc).isoformat(),
    )
    (out_dir / "chunking_report.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "[%s] Stage 3.7 done — %d articles → %d chunks | rule=%d semantic=%d | avg=%.1f words",
        law_entry.law_id, len(articles), total, rule_based_count, semantic_count, avg_wc,
    )
    return report
