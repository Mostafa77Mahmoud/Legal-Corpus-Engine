# Stage 4 — Human Review Export

## Overview

Stage 4 takes the output of Stages 3 (enrichment) and 3.7 (chunking) and produces
reviewer-ready files that the legal team can open in Excel/Numbers or any JSON viewer
to manually QA the AI-generated metadata before the corpus is considered production-ready.

---

## Input

| File | Source Stage |
|------|-------------|
| `data/enriched_articles/{LAW_ID}/articles.json` | Stage 3 |
| `data/chunks/{LAW_ID}/chunks.json` | Stage 3.7 |

---

## Output

All files are written to `data/human_review/{LAW_ID}/`:

| File | Description |
|------|-------------|
| `articles_review.json` | All articles + enrichment metadata + empty review fields |
| `articles_review.csv` | Same data as CSV (Excel-friendly, UTF-8 BOM) |
| `chunks_review.json` | All chunks + article metadata + empty review fields |
| `chunks_review.csv` | Same data as CSV (Excel-friendly, UTF-8 BOM) |
| `review_manifest.json` | Summary statistics + field-level review instructions |

---

## Review Fields

Every record in the review files contains two empty fields for the reviewer to fill:

| Field | Values | Description |
|-------|--------|-------------|
| `review_status` | `approved` / `needs_edit` / `rejected` / `""` | Overall QA decision for this record |
| `review_notes` | free text | Explanation or correction notes |

---

## Article Review Fields

| Field | Description |
|-------|-------------|
| `article_id` | Unique ID (e.g. `EG_PDPL_008`) |
| `article_number` | Numeric article number |
| `article_number_raw` | Original raw marker from the text |
| `article_type` | `issuance` or `main` |
| `article_category` | AI-assigned legal category (Arabic) |
| `topic` | AI-assigned topic (Arabic) |
| `keywords` | Pipe-separated keywords |
| `legal_entities` | Pipe-separated legal entities mentioned |
| `article_summary` | AI-generated summary (Arabic) |
| `is_repealed` | Whether the article is repealed |
| `word_count` / `char_count` | Text length metrics |
| `chunk_count` | How many chunks this article was split into |
| `enrichment_error` | Error message if Stage 3 enrichment failed |
| `text` | Full article text |

---

## Chunk Review Fields

| Field | Description |
|-------|-------------|
| `chunk_id` | Unique ID (e.g. `EG_PDPL_008_C001`) |
| `article_id` | Parent article ID |
| `chunk_index` | 0-based index within the article |
| `chunk_total` | Total chunks for the parent article |
| `has_overlap` | Whether this chunk carries overlap text from the previous chunk |
| `word_count` / `char_count` | Chunk size metrics |
| `text` | Chunk text (including overlap prefix if `has_overlap=true`) |
| *(inherited)* | `topic`, `keywords`, `article_category`, `legal_entities`, `is_repealed` |

---

## Review Checklist

When reviewing, the team should verify:

1. **`topic`** — يعكس محتوى المادة الرئيسي بدقة؟
2. **`keywords`** — مكتملة وغير مكررة وذات صلة؟
3. **`article_category`** — يتطابق مع الفصل/الباب الذي تنتمي إليه المادة؟
4. **`article_summary`** — دقيق وغير مضلل وليس مجرد إعادة صياغة؟
5. **`legal_entities`** — تشمل جميع الجهات القانونية المذكورة في المادة؟
6. **Chunk text** — كل chunk مفهوم بشكل مستقل دون الحاجة لسياق خارجي؟

---

## Running

Stage 4 runs automatically as part of `run_pilot.py`:

```bash
python run_pilot.py EG_PDPL
```

To run Stage 4 in isolation (requires Stages 3 and 3.7 to have run first):

```python
from config.law_registry import get_law
from pipeline import stage_4_human_review

law = get_law("EG_PDPL")
report = stage_4_human_review.run(law)
print(report)
```

---

## Next Stage

After human review is complete and all records are marked `approved`, the pipeline
proceeds to **Stage 5: Final Corpus Export** — which assembles the production-ready
JSON/JSONL corpus files for vector embedding and search indexing.
