# Stage 3 — Metadata Enrichment

## Overview

Stage 3 enriches each article produced by Stage 2 with structured metadata using a single Gemini LLM call per article.  The metadata powers semantic search, legal concept retrieval, and AI reasoning in downstream stages.

---

## Input / Output

| | Path |
|---|---|
| **Input** | `data/split_articles/{LAW_ID}/articles.json` |
| **Output** | `data/enriched_articles/{LAW_ID}/articles.json` |
| **Report** | `data/enriched_articles/{LAW_ID}/enrichment_report.json` |

---

## Metadata Fields Added

Each article in the output JSON contains all original Stage 2 fields plus:

| Field | Type | Description |
|---|---|---|
| `topic` | string | Main topic of the article (2–5 Arabic words) |
| `keywords` | list[str] | 3–8 key legal terms from the article text |
| `article_summary` | string | 1–2 sentence Arabic summary |
| `article_category` | string | One of 9 categories (see below) |
| `legal_entities` | list[str] | Named legal bodies/persons mentioned |
| `enrichment_model` | string | Gemini model used |
| `enrichment_error` | str\|null | Error message if enrichment failed |

### Article Categories

| Category | Arabic | Description |
|---|---|---|
| `تعريف` | Definition | Article defines legal terms |
| `حق` | Right | Grants a right to a person/entity |
| `التزام` | Obligation | Imposes a duty or obligation |
| `إجراء` | Procedure | Defines a process or procedure |
| `عقوبة` | Penalty | Criminal or civil penalty provision |
| `تنظيمية` | Regulatory | Administrative/regulatory provision |
| `انتقالية` | Transitional | Transitional or time-limited provision |
| `إصدار` | Issuance | Promulgation/publication article |
| `أخرى` | Other | Does not fit the above categories |

---

## Gemini Prompt

```
أنت نظام ذكاء اصطناعي متخصص في تحليل النصوص القانونية المصرية.

حلل المادة القانونية التالية وأعد إجابتك بتنسيق JSON فقط.

معلومات القانون:
- اسم القانون: {law_name}
- معرّف المادة: {article_id}
- نوع المادة: {article_type}

نص المادة:
{text}

أعد JSON بالتنسيق التالي بالضبط:
{
  "topic": "...",
  "keywords": ["..."],
  "article_summary": "...",
  "article_category": "...",
  "legal_entities": ["..."]
}
```

- Temperature: 0.0 (deterministic)
- Max output tokens: 8192
- One call per article

---

## Cache Behaviour

Stage 3 checks whether `data/enriched_articles/{LAW_ID}/articles.json` already exists.  Articles that already have a valid `enrichment_model` (and no `enrichment_error`) are **skipped** — only new or failed articles are re-enriched.

Pass `force_reenrich=True` to `stage_3_enrich.run()` to bypass the cache and re-enrich all articles.

The output file is written after **every article** (not at the end) — a crash mid-run is safe; the next run resumes from where it left off.

---

## API Cost Estimates

Based on EG_PDPL (56 articles) at the gemini-3.5-flash pricing:

| Law | Articles | Est. calls | Est. cost |
|---|---|---|---|
| EG_PDPL | 56 | 56 | ~$0.04 |
| EG_ESIGN | 30 | 30 | ~$0.02 |
| EG_LABOR | 218 | 218 | ~$0.14 |
| EG_CIVIL_CODE | 686 | 686 | ~$0.45 |

With 4 free-tier keys × 20 RPD each = 80 calls/day:
- EG_PDPL → 1 day
- EG_CIVIL_CODE → 9 days

**Optimization:** Use `delay_seconds=1.5` (default) between calls to spread load across keys and avoid RPM limits.

---

## Rate Limit Handling

Stage 3 uses the same `KeyManager` as Stage 1.  When an RPM or RPD limit is hit, the manager automatically rotates to the next available key.  If all keys are exhausted, it waits for the earliest cooldown to expire.

---

## Pilot Results (EG_PDPL)

| Metric | Value |
|---|---|
| Total articles | 56 |
| Enriched | 56 |
| Failed | 0 |
| Coverage | 100% |
| Stage 3 cost | ~$0.04 |

### Sample Enriched Article

```json
{
  "article_id": "EG_PDPL_008",
  "law_id": "EG_PDPL",
  "article_number": 1,
  "article_type": "main",
  "text": "...",
  "topic": "حفظ البيانات الشخصية",
  "keywords": ["حفظ البيانات", "المتحكم", "موافقة", "إفصاح"],
  "article_summary": "تلزم المادة المتحكم بحفظ البيانات الشخصية بصورة آمنة وعدم الإفصاح عنها إلا بموافقة صريحة أو بموجب القانون.",
  "article_category": "التزام",
  "legal_entities": ["المتحكم", "الشخص المعني"],
  "enrichment_model": "gemini-3.5-flash",
  "enrichment_error": null
}
```

---

## Error Handling

If a Gemini call fails or returns malformed JSON:
- `enrichment_error` is set to the error message
- `topic`, `keywords`, `article_summary`, `legal_entities` are set to empty defaults
- `article_category` defaults to `"أخرى"`
- The article is saved in the output file so the next run can retry only failed articles

---

## Running Stage 3

Stage 3 runs automatically as part of the full pilot:

```bash
python run_pilot.py EG_PDPL
```

To run Stage 3 alone (for re-enrichment or testing):

```python
from utils.cost_tracker import CostTracker
from config.law_registry import get_law
from pipeline import stage_3_enrich

law = get_law("EG_PDPL")
tracker = CostTracker()
report = stage_3_enrich.run(law_entry=law, cost_tracker=tracker)
print(f"Enriched: {report.enriched} | Cost: ${report.total_cost_usd:.4f}")
```
