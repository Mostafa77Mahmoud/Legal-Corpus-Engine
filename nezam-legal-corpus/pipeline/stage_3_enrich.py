"""
Stage 3 — Metadata Enrichment  (Batch Mode + Structured Output)
================================================================
Input:  data/split_articles/{LAW_ID}/articles.json
Output: data/enriched_articles/{LAW_ID}/articles.json
        data/enriched_articles/{LAW_ID}/enrichment_report.json

التحسينات المطبّقة (بناءً على Google AI documentation):
══════════════════════════════════════════════════════════
1. Structured Output عبر response_schema:
   - API يضمن JSON صالحاً دائماً — لا parsing هش ولا regex
   - Schema يعرّف enum لـ article_category → لا تصنيفات خاطئة
   - propertyOrdering لضمان ترتيب ثابت في الـ output

2. System Instruction محسّن:
   - دور المحلل القانوني المصري (منفصل عن مهمة التحليل)
   - شرح مفصّل لكل تصنيف من article_category مع أمثلة
   - يمنع الـ model من الخلط بين التصنيفات المتشابهة

3. Batch Prompt محسّن:
   - XML tags لتحديد حدود كل مادة (أفضل من Markdown)
   - مختصر — الـ role والقواعد في system_instruction
   - استخدام article_id كـ key في الـ prompt

4. max_output_tokens = 65536 (أقصى ما يتحمّله النموذج):
   - يمنح النموذج "أريحية كاملة" في الرد
   - لا خطر بتر الـ batch مهما كان حجمه

5. Thinking mode قابل للضبط:
   - ENRICH_THINKING_LEVEL="LOW" (gemini-3.x) لتحسين التصنيف القانوني
   - ENRICH_THINKING_BUDGET=1024 (gemini-2.5-x)
   - افتراضياً: None (model auto-decides)

6. temperature = 0.0:
   - مهام extraction و classification = لا إبداع مطلوب
   - greedy decoding → أقصى دقة في الـ output
   - مؤكّد من Google docs: "0 or less than 1 for JSON extraction"

آلية الـ fallback:
- batch فشل → single call لكل مادة منفردة
- single call فشل → ArticleMetadata فارغة مع رسالة خطأ
- حفظ بعد كل batch (crash-safe)
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from config.law_registry import LawEntry
from config.taxonomy import CONCEPT_LIST, validate_concepts
from config.settings import (
    ENRICH_BATCH_SIZE,
    ENRICH_THINKING_BUDGET,
    ENRICH_THINKING_LEVEL,
    ENRICHED_ARTICLES_DIR,
    FALLBACK_MODEL,
    GEMINI_MAX_OUTPUT_TOKENS,
    PRIMARY_MODEL,
    SPLIT_ARTICLES_DIR,
)
from utils.cost_tracker import CostTracker
from utils.llm_client import QuotaExhaustedError, generate_text
from utils import key_manager as _km
from utils.cross_refs import extract_cross_refs

import logging
logger = logging.getLogger(__name__)


# ── Valid category values (mirrors schema enum) ───────────────────────────────

_VALID_CATEGORIES = frozenset({
    "تعريف", "حق", "التزام", "إجراء", "عقوبة",
    "تنظيمية", "انتقالية", "إصدار", "أخرى",
})


# ── Structured Output Schemas ─────────────────────────────────────────────────
# مصدر: https://ai.google.dev/gemini-api/docs/structured-output

_ARTICLE_METADATA_PROPERTIES: dict = {
    "topic": {
        "type": "string",
        "description": "الموضوع الرئيسي للمادة في 2-5 كلمات عربية",
    },
    "keywords": {
        "type": "array",
        "items": {"type": "string"},
        "description": "3-8 مصطلحات قانونية جوهرية مذكورة في نص المادة",
    },
    "article_summary": {
        "type": "string",
        "description": "ملخص موضوعي للمادة في جملة أو جملتين بالعربية",
    },
    "article_category": {
        "type": "string",
        "enum": ["تعريف", "حق", "التزام", "إجراء", "عقوبة", "تنظيمية", "انتقالية", "إصدار", "أخرى"],
        "description": "تصنيف المادة وفق وظيفتها القانونية الرئيسية",
    },
    "legal_entities": {
        "type": "array",
        "items": {"type": "string"},
        "description": "الجهات والهيئات والأشخاص الاعتباريين المذكورون صراحةً",
    },
    "concepts": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Legal concept tags in English (snake_case). "
            "3-6 tags from the article content. "
            "Examples: contract_validity, electronic_signature, data_protection, "
            "civil_liability, legal_capacity, due_process, penalty, consent"
        ),
    },
    "applicable_to": {
        "type": "array",
        "items": {
            "type": "string",
            "enum": [
                "civil", "commercial", "criminal", "administrative",
                "employment", "family", "real_estate", "digital", "procedural",
            ],
        },
        "description": (
            "Legal domains this article applies to. "
            "Choose from the enum values only. "
            "1-3 values that best fit the article scope."
        ),
    },
}

_METADATA_REQUIRED = [
    "topic", "keywords", "article_summary", "article_category",
    "legal_entities", "concepts", "applicable_to",
]
_METADATA_ORDER = [
    "topic", "keywords", "article_summary", "article_category",
    "legal_entities", "concepts", "applicable_to",
]

# Schema للـ batch: مصفوفة كل عنصر فيها {article_id + metadata}
_BATCH_SCHEMA: dict = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "article_id": {
                "type": "string",
                "description": "معرّف المادة كما ورد في الطلب",
            },
            **_ARTICLE_METADATA_PROPERTIES,
        },
        "required": ["article_id"] + _METADATA_REQUIRED,
        "propertyOrdering": ["article_id"] + _METADATA_ORDER,
    },
    "description": "تحليل كل مادة قانونية بنفس ترتيب ورودها",
}

# Schema للـ single: object واحد بدون article_id
_SINGLE_SCHEMA: dict = {
    "type": "object",
    "properties": _ARTICLE_METADATA_PROPERTIES,
    "required": _METADATA_REQUIRED,
    "propertyOrdering": _METADATA_ORDER,
}


# ── System Instruction ─────────────────────────────────────────────────────────
# منفصل عن المهمة — يعرّف الدور ومنهجية التصنيف
# مصدر best practice: Google AI prompting strategies doc

_SYSTEM_INSTRUCTION = """\
أنت محلل قانوني متخصص في التشريعات المصرية وعلوم الفقه والقانون.

## دورك
تحليل وتصنيف نصوص القانون المصري بدقة عالية لبناء قاعدة بيانات قانونية آلية.

## معايير التصنيف (article_category)

اختر التصنيف الأدق لوظيفة المادة القانونية الرئيسية:

| التصنيف | الوظيفة | مثال |
|---------|---------|------|
| **تعريف** | تعرّف مصطلحاً أو تحدد نطاق التطبيق | "يُقصد بالبيانات الشخصية..." |
| **حق** | يمنح حقاً أو يحمي مصلحة للأفراد | "للمواطن الحق في الاطلاع على..." |
| **التزام** | يفرض واجباً أو حظراً على جهة | "يلتزم المتحكم بالإخطار خلال..." |
| **إجراء** | يصف خطوات عملية أو وقائع إدارية | "تتولى الهيئة إصدار التراخيص..." |
| **عقوبة** | جزاءات ومخالفات وغرامات | "يعاقب بالغرامة من..." |
| **تنظيمية** | هياكل تنظيمية واختصاصات الجهات | "تُنشأ هيئة مستقلة تتبع..." |
| **انتقالية** | أحكام مؤقتة أو خاصة بالتطبيق | "تستمر الأوضاع القائمة لمدة..." |
| **إصدار** | ديباجة القانون أو صيغة الإصدار | "رئيس الجمهورية، بعد الاطلاع..." |
| **أخرى** | لا ينطبق عليها أي مما سبق | — |

## حقل concepts (مفاهيم قانونية — إنجليزي)
أعد 3-6 tags من القائمة التالية فقط — لا تخترع مفاهيم خارجها:

{concept_list}

### تمييزات مهمة في concepts:
- `fine` = غرامة مالية عقوبةً على مخالفة — لا تستخدمها لـ رسم أو مقابل خدمة
- `administrative_penalty` = جزاء إداري (توقيف ترخيص، إلغاء، إنذار)
- `licensing` = منح التراخيص أو شروطها — لا `fine`
- `fees` غير موجودة — استخدم `licensing` أو `regulatory_compliance` للمواد عن رسوم الخدمات

## حقل applicable_to (نطاق التطبيق)
اختر 1-3 مجالات من القائمة المحددة فقط:
civil، commercial، criminal، administrative، employment، family، real_estate، digital، procedural.

## قواعد الجودة
- **keywords**: مصطلحات من داخل النص فقط، لا تضف مصطلحات خارجية
- **legal_entities**: الجهات المذكورة صراحةً في نص المادة فقط
- **article_summary**: موضوعي ومحايد، بصيغة المضارع المبني للمعلوم
- **concepts**: اختر فقط من القائمة أعلاه — لا قيم خارجها\
"""

# Inject the live taxonomy list so the model sees the exact valid vocabulary
_SYSTEM_INSTRUCTION = _SYSTEM_INSTRUCTION.format(
    concept_list=", ".join(CONCEPT_LIST)
)


# ── Prompts ────────────────────────────────────────────────────────────────────
# Long-context best practice: "Question Sandwich" — task stated BEFORE the
# documents AND repeated AFTER them. Improves model attention on long batches.
# Source: https://ai.google.dev/gemini-api/docs/long-context

_BATCH_PROMPT = """\
## المهمة
حلّل {count} مادة قانونية من قانون {law_name} — أعد مصفوفة JSON، عنصر واحد لكل مادة.

<articles law="{law_id}" count="{count}">
{articles_block}
</articles>

## تذكير
أعد مصفوفة JSON من {count} عنصراً بنفس ترتيب المواد أعلاه.\
"""

_ARTICLE_BLOCK = """\
  <article id="{article_id}" type="{article_type}" words="{word_count}">
{text}
  </article>\
"""

_SINGLE_PROMPT = """\
حلّل المادة القانونية التالية من قانون {law_name} وأعد JSON واحداً.

<article id="{article_id}" type="{article_type}" law="{law_id}">
{text}
</article>\
"""


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ArticleMetadata:
    topic: str = ""
    keywords: list[str] = field(default_factory=list)
    article_summary: str = ""
    article_category: str = "أخرى"
    legal_entities: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    applicable_to: list[str] = field(default_factory=list)
    enrichment_model: str = ""
    enrichment_error: str | None = None
    enrichment_status: str = ""
    # "completed" — enrichment succeeded and data is trustworthy
    # "failed"    — enrichment was attempted but errored (will be retried on resume)
    # ""          — article was never attempted (pipeline died before reaching it)


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


# ── Metadata parsing ───────────────────────────────────────────────────────────

_VALID_APPLICABLE_TO = frozenset({
    "civil", "commercial", "criminal", "administrative",
    "employment", "family", "real_estate", "digital", "procedural",
})


def _parse_metadata(data: dict[str, Any], model: str) -> ArticleMetadata:
    """Convert a JSON dict from structured output into ArticleMetadata."""
    category = data.get("article_category", "أخرى")
    if category not in _VALID_CATEGORIES:
        category = "أخرى"

    # concepts: validate against taxonomy.py — reject anything not in LEGAL_CONCEPTS
    raw_concepts = data.get("concepts", []) or []
    normalised_concepts = [
        str(c).strip().lower().replace(" ", "_")
        for c in raw_concepts
        if c and str(c).strip()
    ]
    valid_concepts, rejected_concepts = validate_concepts(normalised_concepts)
    if rejected_concepts:
        logger.debug("Concepts rejected (not in taxonomy): %s", rejected_concepts)
    concepts = valid_concepts[:10]

    # applicable_to: keep only enum-valid values
    raw_applicable = data.get("applicable_to", []) or []
    applicable_to = [
        str(a).strip().lower()
        for a in raw_applicable
        if str(a).strip().lower() in _VALID_APPLICABLE_TO
    ][:5]

    return ArticleMetadata(
        topic=str(data.get("topic", ""))[:100],
        keywords=[str(k)[:80] for k in data.get("keywords", [])[:10]],
        article_summary=str(data.get("article_summary", ""))[:600],
        article_category=category,
        legal_entities=[str(e)[:80] for e in data.get("legal_entities", [])[:15]],
        concepts=concepts,
        applicable_to=applicable_to,
        enrichment_model=model,
        enrichment_status="completed",
    )


# ── Batch enrichment ──────────────────────────────────────────────────────────

def _build_batch_prompt(articles: list[dict[str, Any]], law_entry: LawEntry) -> str:
    blocks = []
    for art in articles:
        text = art.get("text", "").strip()
        wc = len(text.split())
        blocks.append(_ARTICLE_BLOCK.format(
            article_id=art["article_id"],
            article_type=art.get("article_type", "main"),
            word_count=wc,
            text=text,
        ))
    return _BATCH_PROMPT.format(
        law_name=law_entry.law_name_ar,
        law_id=law_entry.law_id,
        count=len(articles),
        articles_block="\n".join(blocks),
    )


def _enrich_batch(
    articles: list[dict[str, Any]],
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    model: str,
    fast_fail_on_quota: bool = False,
) -> dict[str, ArticleMetadata]:
    """
    Send a batch of articles in ONE Gemini call using structured output.

    Returns dict[article_id → ArticleMetadata].
    Missing article_ids in the response are excluded (caller will fallback).

    Raises QuotaExhaustedError when fast_fail_on_quota=True and all keys hit RPD,
    so the caller can switch to a fallback model immediately.
    """
    prompt = _build_batch_prompt(articles, law_entry)
    raw = generate_text(
        prompt=prompt,
        cost_tracker=cost_tracker,
        stage="stage_3",
        law_id=law_entry.law_id,
        model_name=model,
        temperature=0.0,
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,   # 65536 — full breathing room
        thinking_budget=ENRICH_THINKING_BUDGET,        # None = model auto-decides
        thinking_level=ENRICH_THINKING_LEVEL,          # e.g. "LOW" for gemini-3.x
        response_schema=_BATCH_SCHEMA,
        system_instruction=_SYSTEM_INSTRUCTION,
        fast_fail_on_quota=fast_fail_on_quota,
    )

    # Structured output → guaranteed valid JSON array
    parsed_list: list[dict[str, Any]] = json.loads(raw)
    results: dict[str, ArticleMetadata] = {}

    for item in parsed_list:
        aid = item.get("article_id", "")
        if not aid:
            continue
        try:
            results[aid] = _parse_metadata(item, model)
        except Exception as exc:
            logger.warning("[%s] Failed to parse batch item for %s: %s", law_entry.law_id, aid, exc)

    return results


def _enrich_single(
    article: dict[str, Any],
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    model: str,
) -> ArticleMetadata:
    """Fallback: enrich a single article in its own Gemini call."""
    prompt = _SINGLE_PROMPT.format(
        law_name=law_entry.law_name_ar,
        article_id=article["article_id"],
        article_type=article.get("article_type", "main"),
        law_id=law_entry.law_id,
        text=article.get("text", "").strip(),
    )
    raw = generate_text(
        prompt=prompt,
        cost_tracker=cost_tracker,
        stage="stage_3",
        law_id=law_entry.law_id,
        model_name=model,
        temperature=0.0,
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,   # 65536 — full breathing room
        thinking_budget=ENRICH_THINKING_BUDGET,
        thinking_level=ENRICH_THINKING_LEVEL,
        response_schema=_SINGLE_SCHEMA,
        system_instruction=_SYSTEM_INSTRUCTION,
    )
    try:
        data = json.loads(raw)
        return _parse_metadata(data, model)
    except Exception as exc:
        return ArticleMetadata(enrichment_model=model, enrichment_error=str(exc))


# ── Output assembly ───────────────────────────────────────────────────────────

def _assemble_output(
    articles: list[dict[str, Any]],
    cached_by_id: dict[str, dict[str, Any]],
    enriched_meta: dict[str, ArticleMetadata],
    model: str,
) -> list[dict[str, Any]]:
    result = []
    for art in articles:
        aid = art["article_id"]
        if aid in enriched_meta:
            merged = {**art, **asdict(enriched_meta[aid])}
        elif aid in cached_by_id:
            merged = dict(cached_by_id[aid])
        else:
            merged = {
                **art,
                **asdict(ArticleMetadata(enrichment_model=model, enrichment_error="not_reached")),
            }
        # Always (re-)extract explicit_cross_refs from article text via regex
        # This is free (no API call) and ensures the field is always present
        # and up-to-date even for cache hits from previous runs.
        merged["explicit_cross_refs"] = extract_cross_refs(merged.get("text", ""))
        result.append(merged)
    return result


# ── Public run function ───────────────────────────────────────────────────────

def _build_model_list() -> list[str]:
    """
    Return the ordered list of models Stage 3 will try per batch.

    Strategy:
    - Always start with PRIMARY_MODEL.
    - If FALLBACK_MODEL is different, append it.
    - Deduplicates while preserving order.
    """
    seen: set[str] = set()
    models: list[str] = []
    for m in (PRIMARY_MODEL, FALLBACK_MODEL):
        if m and m not in seen:
            seen.add(m)
            models.append(m)
    return models


def run(
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    force_reenrich: bool = False,
    batch_size: int = ENRICH_BATCH_SIZE,
    delay_seconds: float = 0.5,
) -> EnrichmentReport:
    """
    Enrich all articles for *law_entry* with AI-generated metadata.

    Parameters
    ----------
    force_reenrich : bool
        If True, ignore cache and re-enrich all articles.
    batch_size : int
        Articles per Gemini call.  Default raised to ENRICH_BATCH_SIZE=50 —
        packs ~20 K output tokens per call (well within 65 K limit) and
        reduces total API calls from ~104 → ~19 for EG_CIVIL_CODE.
    delay_seconds : float
        Sleep between batch calls for rate-limit courtesy.

    Model rotation
    --------------
    When ALL keys exhaust their daily RPD quota for the primary model,
    Stage 3 automatically switches to FALLBACK_MODEL (e.g. gemini-3.5-flash
    ↔ gemini-2.5-flash) and resets the key pool state so the fresh-quota
    keys become available again.  If both models are exhausted the pipeline
    blocks as usual until UTC midnight.
    """
    model_list = _build_model_list()
    model_idx  = 0               # index into model_list; advances on quota exhaustion
    model      = model_list[0]   # current active model

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
                # Only trust articles with explicit enrichment_status="completed".
                # Older cache entries lacking the field fall back to the proxy check
                # (enrichment_model set + no error) for backwards compatibility.
                status = art.get("enrichment_status", "")
                if status == "completed" or (
                    not status
                    and art.get("enrichment_model")
                    and not art.get("enrichment_error")
                ):
                    cached_by_id[art["article_id"]] = art
        except Exception:
            pass

    to_enrich = [a for a in articles if a["article_id"] not in cached_by_id or force_reenrich]

    logger.info(
        "[%s] Stage 3: %d total | %d cached | %d to enrich | batch=%d | models=%s",
        law_entry.law_id, len(articles), len(cached_by_id), len(to_enrich),
        batch_size, " → ".join(model_list),
    )

    # ── Batch enrichment loop ─────────────────────────────────────────────────
    enriched_meta: dict[str, ArticleMetadata] = {}
    total_api_calls = 0
    total_batches = (len(to_enrich) + batch_size - 1) // batch_size

    for batch_start in range(0, len(to_enrich), batch_size):
        batch = to_enrich[batch_start: batch_start + batch_size]
        batch_num = batch_start // batch_size + 1

        logger.info(
            "[%s] Batch %d/%d [%s]: %s → %s",
            law_entry.law_id, batch_num, total_batches, model,
            batch[0]["article_id"], batch[-1]["article_id"],
        )

        # ── Try batch call — with model rotation on quota exhaustion ──────────
        batch_results: dict[str, ArticleMetadata] = {}
        batch_ok = False

        for _model_attempt in range(len(model_list) + 1):
            # Last pass: no more fallback models — use blocking wait (fast_fail=False)
            is_last_model = (_model_attempt >= len(model_list) - 1)
            try:
                batch_results = _enrich_batch(
                    batch, law_entry, cost_tracker, model,
                    fast_fail_on_quota=not is_last_model,
                )
                total_api_calls += 1
                logger.info(
                    "[%s] Batch %d OK [%s]: %d/%d articles returned",
                    law_entry.law_id, batch_num, model,
                    len(batch_results), len(batch),
                )
                batch_ok = True
                break

            except QuotaExhaustedError:
                # All keys RPD-exhausted for current model — try next model
                next_idx = model_idx + 1
                if next_idx < len(model_list):
                    next_model = model_list[next_idx]
                    logger.warning(
                        "[%s] Batch %d: model '%s' daily quota exhausted (all %d keys). "
                        "Switching to '%s' and resetting key pool.",
                        law_entry.law_id, batch_num, model, 4, next_model,
                    )
                    model_idx = next_idx
                    model = next_model
                    # Reset key manager: clears per-process cooldown state so
                    # the new model starts with a fresh key pool (each model has
                    # independent RPD quota — exhausting model A does not affect B).
                    _km.reset_manager()
                else:
                    # All models exhausted — fall through to blocking mode
                    logger.warning(
                        "[%s] Batch %d: all models quota-exhausted — blocking until reset.",
                        law_entry.law_id, batch_num,
                    )
                    _km.reset_manager()
                    # Next loop iteration will use is_last_model=True → no fast_fail
                continue

            except Exception as exc:
                logger.warning(
                    "[%s] Batch %d failed [%s] (%s) — will try sub-batches before single-article",
                    law_entry.law_id, batch_num, model, exc,
                )
                break

        # ── Sub-batch halving: before going single-article, try half-size batches ─
        # Prevents rapid quota exhaustion when a large batch fails due to truncated
        # JSON output (output token limit exceeded). Halving reduces output size by
        # ~50% per level, making it much more likely to succeed within token limits.
        missing_arts = [a for a in batch if a["article_id"] not in batch_results]
        if missing_arts and not batch_ok and len(missing_arts) > 1:
            sub_size = max(2, len(missing_arts) // 2)
            sub_total = (len(missing_arts) + sub_size - 1) // sub_size
            logger.info(
                "[%s] Batch %d: trying %d sub-batches of ≤%d articles",
                law_entry.law_id, batch_num, sub_total, sub_size,
            )
            for sub_start in range(0, len(missing_arts), sub_size):
                sub_batch = missing_arts[sub_start: sub_start + sub_size]
                sub_num = sub_start // sub_size + 1
                logger.info(
                    "[%s] Batch %d sub-batch %d/%d [%s]: %s → %s",
                    law_entry.law_id, batch_num, sub_num, sub_total, model,
                    sub_batch[0]["article_id"], sub_batch[-1]["article_id"],
                )
                try:
                    sub_res = _enrich_batch(
                        sub_batch, law_entry, cost_tracker, model,
                        fast_fail_on_quota=False,
                    )
                    total_api_calls += 1
                    batch_results.update(sub_res)
                    logger.info(
                        "[%s] Sub-batch %d OK: %d/%d articles returned",
                        law_entry.law_id, sub_num, len(sub_res), len(sub_batch),
                    )
                except Exception as sub_exc:
                    logger.warning(
                        "[%s] Sub-batch %d failed (%s) — single-article for these %d",
                        law_entry.law_id, sub_num, sub_exc, len(sub_batch),
                    )

        # ── Single-article fallback for anything still missing ─────────────────
        for art in batch:
            aid = art["article_id"]
            if aid in batch_results:
                enriched_meta[aid] = batch_results[aid]
                continue

            logger.info("[%s] Single fallback for %s [%s]", law_entry.law_id, aid, model)
            try:
                meta = _enrich_single(art, law_entry, cost_tracker, model)
                total_api_calls += 1
            except Exception as exc:
                logger.error("[%s] Single failed for %s: %s", law_entry.law_id, aid, exc)
                meta = ArticleMetadata(
                    enrichment_model=model,
                    enrichment_error=str(exc),
                    enrichment_status="failed",
                )
            enriched_meta[aid] = meta

            # ── Intra-batch checkpoint (single-article fallback only) ──────────
            # Save after every successful single-article enrichment so a crash or
            # quota kill never loses more than one article of completed work.
            if meta.enrichment_status == "completed":
                assembled = _assemble_output(articles, cached_by_id, enriched_meta, model)
                out_path.write_text(
                    json.dumps(assembled, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            time.sleep(0.5)

        # ── Crash-safe save after each batch ──────────────────────────────────
        assembled = _assemble_output(articles, cached_by_id, enriched_meta, model)
        out_path.write_text(json.dumps(assembled, ensure_ascii=False, indent=2), encoding="utf-8")

        if batch_start + batch_size < len(to_enrich):
            time.sleep(delay_seconds)

    # ── Final stats ───────────────────────────────────────────────────────────
    enriched_ok  = sum(1 for m in enriched_meta.values() if not m.enrichment_error)
    failed_count = sum(1 for m in enriched_meta.values() if m.enrichment_error)
    skipped      = len(articles) - len(to_enrich)

    stage_cost = cost_tracker.summary().get("by_stage", {}).get("stage_3", {}).get("cost_usd", 0.0)
    report = EnrichmentReport(
        law_id=law_entry.law_id,
        total_articles=len(articles),
        enriched=enriched_ok,
        skipped_cache=skipped,
        failed=failed_count,
        total_cost_usd=stage_cost,
        enriched_at=datetime.now(timezone.utc).isoformat(),
        model=model,
        batch_size=batch_size,
        total_api_calls=total_api_calls,
    )
    (out_dir / "enrichment_report.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8",
    )

    logger.info(
        "[%s] Stage 3 done — enriched=%d cached=%d failed=%d | %d API calls | $%.4f",
        law_entry.law_id, enriched_ok, skipped, failed_count, total_api_calls, stage_cost,
    )
    return report
