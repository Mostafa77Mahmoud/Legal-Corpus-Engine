"""
Stage 3 — Metadata Enrichment  (Batch Mode)
=============================================
Input:  data/split_articles/{LAW_ID}/articles.json
Output: data/enriched_articles/{LAW_ID}/articles.json
        data/enriched_articles/{LAW_ID}/enrichment_report.json

بدلاً من إرسال مادة واحدة لكل Gemini call، يُرسل هذا الـ stage
ENRICH_BATCH_SIZE مادة في طلب واحد (افتراضي: 10).

فوائد الـ batch:
- تخفيض API calls بنسبة ~85% لـ 56 مادة (56 → 6 calls)
- تخفيض التكلفة: system prompt يُرسل مرة واحدة لكل batch
- سرعة أعلى: أقل delays وأقل تعاملات مع rate limits
- سياق مشترك: Gemini يرى مجموعة مواد فيكون أكثر اتساقاً في التصنيف

آلية الـ fallback:
- إذا فشل الـ batch → إعادة المحاولة مادة مادة للمواد الفاشلة فقط
- الـ cache يعمل على مستوى المادة الفردية (article_id)
- حفظ تلقائي بعد كل batch (crash-safe)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from config.law_registry import LawEntry
from config.settings import (
    ENRICH_BATCH_SIZE,
    ENRICHED_ARTICLES_DIR,
    PRIMARY_MODEL,
    SPLIT_ARTICLES_DIR,
)
from utils.cost_tracker import CostTracker
from utils.llm_client import generate_text

import logging
logger = logging.getLogger(__name__)


# ── Valid values ───────────────────────────────────────────────────────────────

_VALID_CATEGORIES = {
    "تعريف", "حق", "التزام", "إجراء", "عقوبة",
    "تنظيمية", "انتقالية", "إصدار", "أخرى",
}


# ── Prompts ────────────────────────────────────────────────────────────────────

# Prompt للـ batch (يرسل أكثر من مادة في طلب واحد)
_BATCH_PROMPT_HEADER = """\
أنت نظام ذكاء اصطناعي متخصص في تحليل النصوص القانونية المصرية.

حلل المواد القانونية التالية من {law_name} وأعد إجابتك بتنسيق JSON فقط.

قواعد صارمة:
- article_category: اختر واحدة بالضبط من: تعريف | حق | التزام | إجراء | عقوبة | تنظيمية | انتقالية | إصدار | أخرى
- keywords: 3-8 مصطلحات قانونية جوهرية من نص المادة (لا أقل من 3)
- legal_entities: الجهات والهيئات والأشخاص الاعتباريين المذكورون فقط (قائمة فارغة [] إذا لا يوجد)
- article_summary: جملة أو جملتان بالعربية تلخص المادة
- topic: 2-5 كلمات عربية تصف موضوع المادة الرئيسي
- أعد JSON فقط — لا تكتب أي نص خارج الـ JSON

## المواد ({count} مادة):

{articles_block}

## المطلوب:
أعد JSON object مفتاحه article_id يحتوي تحليل كل مادة:

{{
  "ARTICLE_ID_1": {{
    "topic": "...",
    "keywords": ["...", "..."],
    "article_summary": "...",
    "article_category": "...",
    "legal_entities": ["..."]
  }},
  "ARTICLE_ID_2": {{ ... }}
}}
"""

_ARTICLE_BLOCK_TEMPLATE = """\
### [{article_id}] — نوع: {article_type}
{text}
"""

# Prompt الاحتياطي للمادة الواحدة (fallback)
_SINGLE_PROMPT = """\
أنت نظام ذكاء اصطناعي متخصص في تحليل النصوص القانونية المصرية.

حلل المادة القانونية التالية وأعد إجابتك بتنسيق JSON فقط.

معلومات القانون:
- اسم القانون: {law_name}
- معرّف المادة: {article_id}
- نوع المادة: {article_type}

نص المادة:
{text}

أعد JSON بالتنسيق التالي بالضبط (بدون أي نص إضافي قبله أو بعده):
{{
  "topic": "الموضوع الرئيسي للمادة في 2-5 كلمات عربية",
  "keywords": ["مصطلح1", "مصطلح2", "مصطلح3"],
  "article_summary": "ملخص المادة في جملة أو جملتين.",
  "article_category": "تعريف",
  "legal_entities": ["كيان1", "كيان2"]
}}

قواعد صارمة:
- article_category: اختر واحدة بالضبط من: تعريف | حق | التزام | إجراء | عقوبة | تنظيمية | انتقالية | إصدار | أخرى
- keywords: 3-8 مصطلحات قانونية جوهرية من نص المادة
- legal_entities: الجهات والهيئات والأشخاص الاعتباريين المذكورون (قائمة فارغة [] إذا لا يوجد)
- أعد JSON فقط — لا تكتب أي شيء خارج الـ JSON
"""


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ArticleMetadata:
    topic: str = ""
    keywords: list[str] = field(default_factory=list)
    article_summary: str = ""
    article_category: str = "أخرى"
    legal_entities: list[str] = field(default_factory=list)
    enrichment_model: str = ""
    enrichment_error: str | None = None


@dataclass
class EnrichmentReport:
    law_id: str
    total_articles: int
    enriched: int
    skipped_cache: int
    failed: int
    total_cost_usd: float
    enriched_at: str
    model: str
    batch_size: int
    total_api_calls: int


# ── JSON parsing helpers ───────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    """Remove markdown code fences from Gemini response."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
    return raw.strip()


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Extract the outermost JSON object from a string."""
    raw = _strip_fences(raw)
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")
    return json.loads(raw[start:end])


def _parse_single_metadata(data: dict[str, Any], model: str) -> ArticleMetadata:
    """Convert a parsed JSON dict into ArticleMetadata."""
    category = data.get("article_category", "أخرى")
    if category not in _VALID_CATEGORIES:
        category = "أخرى"
    return ArticleMetadata(
        topic=str(data.get("topic", ""))[:100],
        keywords=[str(k)[:80] for k in data.get("keywords", [])[:10]],
        article_summary=str(data.get("article_summary", ""))[:500],
        article_category=category,
        legal_entities=[str(e)[:80] for e in data.get("legal_entities", [])[:15]],
        enrichment_model=model,
    )


# ── Batch enrichment ──────────────────────────────────────────────────────────

def _build_batch_prompt(
    articles: list[dict[str, Any]],
    law_name: str,
) -> str:
    """Build a single prompt that asks Gemini to enrich multiple articles at once."""
    blocks = []
    for art in articles:
        blocks.append(_ARTICLE_BLOCK_TEMPLATE.format(
            article_id=art["article_id"],
            article_type=art.get("article_type", "main"),
            text=art.get("text", "").strip(),
        ))
    return _BATCH_PROMPT_HEADER.format(
        law_name=law_name,
        count=len(articles),
        articles_block="\n".join(blocks),
    )


def _enrich_batch(
    articles: list[dict[str, Any]],
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    model: str,
) -> dict[str, ArticleMetadata]:
    """
    Send a batch of articles to Gemini in one call.

    Returns a dict mapping article_id → ArticleMetadata.
    Articles missing from the response are NOT included in the returned dict
    (caller should fall back to single enrichment for those).
    """
    prompt = _build_batch_prompt(articles, law_entry.law_name_ar)
    raw = generate_text(
        prompt=prompt,
        cost_tracker=cost_tracker,
        stage="stage_3",
        law_id=law_entry.law_id,
        model_name=model,
    )

    try:
        parsed = _extract_json_object(raw)
    except Exception as exc:
        raise ValueError(f"Batch JSON parse failed: {exc}\nRaw response snippet: {raw[:300]}")

    results: dict[str, ArticleMetadata] = {}
    for art in articles:
        aid = art["article_id"]
        if aid not in parsed:
            logger.warning("[%s] Batch response missing article %s — will fallback", law_entry.law_id, aid)
            continue
        try:
            results[aid] = _parse_single_metadata(parsed[aid], model)
        except Exception as exc:
            logger.warning("[%s] Failed to parse batch result for %s: %s", law_entry.law_id, aid, exc)

    return results


def _enrich_single(
    article: dict[str, Any],
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    model: str,
) -> ArticleMetadata:
    """Fallback: enrich a single article with its own Gemini call."""
    prompt = _SINGLE_PROMPT.format(
        law_name=law_entry.law_name_ar,
        article_id=article["article_id"],
        article_type=article.get("article_type", "main"),
        text=article.get("text", ""),
    )
    raw = generate_text(
        prompt=prompt,
        cost_tracker=cost_tracker,
        stage="stage_3",
        law_id=law_entry.law_id,
        model_name=model,
    )
    try:
        data = _extract_json_object(raw)
        return _parse_single_metadata(data, model)
    except Exception as exc:
        return ArticleMetadata(enrichment_model=model, enrichment_error=str(exc))


# ── Public run function ───────────────────────────────────────────────────────

def run(
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    force_reenrich: bool = False,
    batch_size: int = ENRICH_BATCH_SIZE,
    delay_seconds: float = 1.0,
) -> EnrichmentReport:
    """
    Enrich all articles for *law_entry* with Gemini-generated metadata.

    Parameters
    ----------
    force_reenrich : bool
        If True, re-enrich even articles that have cached metadata.
    batch_size : int
        Number of articles per Gemini call (default: ENRICH_BATCH_SIZE from settings).
    delay_seconds : float
        Sleep between batch calls to respect rate limits.
    """
    model = PRIMARY_MODEL

    # ── Load split articles ───────────────────────────────────────────────────
    articles_path = SPLIT_ARTICLES_DIR / law_entry.law_id / "articles.json"
    if not articles_path.exists():
        raise FileNotFoundError(
            f"Split articles not found: {articles_path}\nRun Stage 2 first."
        )
    articles: list[dict[str, Any]] = json.loads(articles_path.read_text(encoding="utf-8"))

    # ── Load cache ────────────────────────────────────────────────────────────
    out_dir = ENRICHED_ARTICLES_DIR / law_entry.law_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "articles.json"

    cached_by_id: dict[str, dict[str, Any]] = {}
    if out_path.exists() and not force_reenrich:
        try:
            for art in json.loads(out_path.read_text(encoding="utf-8")):
                if art.get("enrichment_model") and not art.get("enrichment_error"):
                    cached_by_id[art["article_id"]] = art
        except Exception:
            pass

    # ── Separate cached vs. need-enrichment ──────────────────────────────────
    to_enrich: list[dict[str, Any]] = []
    for art in articles:
        if art["article_id"] in cached_by_id and not force_reenrich:
            pass   # will be filled from cache when assembling output
        else:
            to_enrich.append(art)

    logger.info(
        "[%s] Stage 3: %d articles total — %d cached, %d to enrich (batch_size=%d)",
        law_entry.law_id, len(articles), len(cached_by_id), len(to_enrich), batch_size,
    )

    # ── Batch enrichment ──────────────────────────────────────────────────────
    enriched_meta: dict[str, ArticleMetadata] = {}   # article_id → metadata
    total_api_calls = 0

    # Process in batches
    for batch_start in range(0, len(to_enrich), batch_size):
        batch = to_enrich[batch_start: batch_start + batch_size]
        batch_ids = [a["article_id"] for a in batch]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(to_enrich) + batch_size - 1) // batch_size

        logger.info(
            "[%s] Batch %d/%d — articles %s … %s",
            law_entry.law_id, batch_num, total_batches, batch_ids[0], batch_ids[-1],
        )

        # ── Try batch call ────────────────────────────────────────────────────
        batch_results: dict[str, ArticleMetadata] = {}
        try:
            batch_results = _enrich_batch(batch, law_entry, cost_tracker, model)
            total_api_calls += 1
        except Exception as exc:
            logger.warning(
                "[%s] Batch %d failed (%s) — falling back to single-article mode for %d articles",
                law_entry.law_id, batch_num, exc, len(batch),
            )

        # ── Fallback: single call for any article missing from batch result ───
        for art in batch:
            aid = art["article_id"]
            if aid in batch_results:
                enriched_meta[aid] = batch_results[aid]
            else:
                logger.info("[%s] Single fallback for %s", law_entry.law_id, aid)
                try:
                    meta = _enrich_single(art, law_entry, cost_tracker, model)
                    total_api_calls += 1
                except Exception as exc:
                    logger.error("[%s] Single enrichment failed for %s: %s", law_entry.law_id, aid, exc)
                    meta = ArticleMetadata(enrichment_model=model, enrichment_error=str(exc))
                enriched_meta[aid] = meta
                time.sleep(0.5)   # small delay between single calls

        # ── Save after each batch (crash-safe) ───────────────────────────────
        assembled = _assemble_output(articles, cached_by_id, enriched_meta, model)
        out_path.write_text(json.dumps(assembled, ensure_ascii=False, indent=2), encoding="utf-8")

        # Rate-limit courtesy delay between batches
        if batch_start + batch_size < len(to_enrich):
            time.sleep(delay_seconds)

    # ── Final assembly ────────────────────────────────────────────────────────
    enriched_articles = _assemble_output(articles, cached_by_id, enriched_meta, model)
    out_path.write_text(json.dumps(enriched_articles, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Stats ─────────────────────────────────────────────────────────────────
    enriched_count = sum(
        1 for aid, m in enriched_meta.items() if not m.enrichment_error
    )
    failed_count = sum(
        1 for aid, m in enriched_meta.items() if m.enrichment_error
    )
    skipped_count = len(articles) - len(to_enrich)

    stage_cost = cost_tracker.summary().get("by_stage", {}).get("stage_3", {}).get("cost_usd", 0.0)
    report = EnrichmentReport(
        law_id=law_entry.law_id,
        total_articles=len(articles),
        enriched=enriched_count,
        skipped_cache=skipped_count,
        failed=failed_count,
        total_cost_usd=stage_cost,
        enriched_at=datetime.now(timezone.utc).isoformat(),
        model=model,
        batch_size=batch_size,
        total_api_calls=total_api_calls,
    )

    report_path = out_dir / "enrichment_report.json"
    report_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "[%s] Stage 3 done — %d enriched, %d cached, %d failed | %d API calls | cost $%.4f",
        law_entry.law_id, enriched_count, skipped_count, failed_count, total_api_calls, stage_cost,
    )
    return report


def _assemble_output(
    articles: list[dict[str, Any]],
    cached_by_id: dict[str, dict[str, Any]],
    enriched_meta: dict[str, ArticleMetadata],
    model: str,
) -> list[dict[str, Any]]:
    """Merge original article data with enrichment metadata, preserving order."""
    result = []
    for art in articles:
        aid = art["article_id"]
        if aid in enriched_meta:
            result.append({**art, **asdict(enriched_meta[aid])})
        elif aid in cached_by_id:
            result.append(cached_by_id[aid])
        else:
            # Article not enriched and not cached — write with empty metadata
            result.append({
                **art,
                **asdict(ArticleMetadata(enrichment_model=model, enrichment_error="not_reached")),
            })
    return result
