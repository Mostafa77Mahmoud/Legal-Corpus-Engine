"""
Stage 3 — Metadata Enrichment
==============================
Input:  data/split_articles/{LAW_ID}/articles.json
Output: data/enriched_articles/{LAW_ID}/articles.json
        data/enriched_articles/{LAW_ID}/enrichment_report.json

One Gemini call per article — extracts topic, keywords, summary, category,
and legal entities.  Already-enriched articles are skipped (cache).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.law_registry import LawEntry
from config.settings import (
    ENRICHED_ARTICLES_DIR,
    PRIMARY_MODEL,
    SPLIT_ARTICLES_DIR,
)
from utils.cost_tracker import CostTracker
from utils.llm_client import generate_text

import logging
logger = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

_ENRICH_PROMPT = """\
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
- legal_entities: الجهات والهيئات والأشخاص الاعتباريين المذكورون (لا تشمل عبارات عامة مثل "الشخص الطبيعي")
- article_summary: اكتب بالعربية فقط، لا تترجم
- أعد JSON فقط — لا تكتب أي شيء خارج الـ JSON
"""

_VALID_CATEGORIES = {
    "تعريف", "حق", "التزام", "إجراء", "عقوبة",
    "تنظيمية", "انتقالية", "إصدار", "أخرى",
}


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


# ── JSON extraction ───────────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict[str, Any]:
    """Extract the first JSON object from a Gemini response string."""
    raw = raw.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")
    return json.loads(raw[start:end])


def _parse_metadata(raw: str, model: str) -> ArticleMetadata:
    """Parse Gemini response into ArticleMetadata; fall back gracefully."""
    try:
        data = _extract_json(raw)
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
    except Exception as exc:
        return ArticleMetadata(
            enrichment_model=model,
            enrichment_error=str(exc),
        )


# ── Core enrichment ───────────────────────────────────────────────────────────

def _enrich_article(
    article: dict[str, Any],
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    model: str,
) -> ArticleMetadata:
    prompt = _ENRICH_PROMPT.format(
        law_name=law_entry.law_name_ar,
        article_id=article["article_id"],
        article_type=article["article_type"],
        text=article["text"],
    )
    raw_response = generate_text(
        prompt=prompt,
        cost_tracker=cost_tracker,
        stage="stage_3",
        law_id=law_entry.law_id,
        model_name=model,
    )
    return _parse_metadata(raw_response, model)


# ── Public run function ───────────────────────────────────────────────────────

def run(
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    force_reenrich: bool = False,
    delay_seconds: float = 1.5,
) -> EnrichmentReport:
    """
    Enrich all articles for *law_entry* with Gemini-generated metadata.

    Parameters
    ----------
    force_reenrich : bool
        If True, re-enrich articles that already have metadata.
    delay_seconds : float
        Sleep between Gemini calls to respect rate limits.

    Returns
    -------
    EnrichmentReport
    """
    model = PRIMARY_MODEL

    # ── Load split articles ───────────────────────────────────────────────────
    split_dir = SPLIT_ARTICLES_DIR / law_entry.law_id
    articles_path = split_dir / "articles.json"
    if not articles_path.exists():
        raise FileNotFoundError(
            f"Split articles not found: {articles_path}\n"
            f"Run Stage 2 first."
        )
    articles: list[dict[str, Any]] = json.loads(articles_path.read_text(encoding="utf-8"))

    # ── Load any existing enriched output (for cache) ─────────────────────────
    out_dir = ENRICHED_ARTICLES_DIR / law_entry.law_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "articles.json"

    cached_by_id: dict[str, dict[str, Any]] = {}
    if out_path.exists() and not force_reenrich:
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            for art in existing:
                if art.get("enrichment_model") and not art.get("enrichment_error"):
                    cached_by_id[art["article_id"]] = art
        except Exception:
            pass

    # ── Enrich articles ───────────────────────────────────────────────────────
    enriched_count = 0
    skipped_count = 0
    failed_count = 0
    enriched_articles: list[dict[str, Any]] = []

    for i, article in enumerate(articles):
        article_id = article["article_id"]

        # Cache hit
        if article_id in cached_by_id and not force_reenrich:
            logger.debug("[%s] Cache hit — skipping %s", law_entry.law_id, article_id)
            enriched_articles.append(cached_by_id[article_id])
            skipped_count += 1
            continue

        logger.info(
            "[%s] Enriching %s (%d/%d)…",
            law_entry.law_id, article_id, i + 1, len(articles),
        )

        try:
            meta = _enrich_article(article, law_entry, cost_tracker, model)
        except Exception as exc:
            logger.error("[%s] Enrichment failed for %s: %s", law_entry.law_id, article_id, exc)
            meta = ArticleMetadata(enrichment_model=model, enrichment_error=str(exc))

        enriched_art = {**article, **asdict(meta)}
        enriched_articles.append(enriched_art)

        if meta.enrichment_error:
            failed_count += 1
            logger.warning("[%s] %s — metadata error: %s", law_entry.law_id, article_id, meta.enrichment_error)
        else:
            enriched_count += 1
            logger.debug("[%s] %s — topic: %s | category: %s", law_entry.law_id, article_id, meta.topic, meta.article_category)

        # Save after each article (crash-safe)
        out_path.write_text(
            json.dumps(enriched_articles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Rate-limit courtesy delay
        if i < len(articles) - 1:
            time.sleep(delay_seconds)

    # ── Write enrichment report ───────────────────────────────────────────────
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
    )
    report_path = out_dir / "enrichment_report.json"
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "[%s] Stage 3 done — %d enriched, %d cached, %d failed | cost $%.4f",
        law_entry.law_id, enriched_count, skipped_count, failed_count, stage_cost,
    )
    return report
