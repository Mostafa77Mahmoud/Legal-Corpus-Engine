# Stage 3.7 — Article Chunking

## Overview

Stage 3.7 splits each enriched article into semantically coherent chunks that are suitable for vector embedding and retrieval-augmented generation (RAG).  Short articles become a single chunk; long articles are split at paragraph and sentence boundaries.

---

## Input / Output

| | Path |
|---|---|
| **Input** | `data/enriched_articles/{LAW_ID}/articles.json` |
| **Output** | `data/chunks/{LAW_ID}/chunks.json` |
| **Report** | `data/chunks/{LAW_ID}/chunking_report.json` |

---

## Chunking Strategy

```
Article text
    │
    ├── word_count ≤ 250  →  single chunk (no split)
    │
    └── word_count >  250  →  split by double-newline paragraphs
                                   │
                                   ├── paragraph ≤ 250 words  →  use as-is
                                   │
                                   └── paragraph >  250 words  →  split by sentence boundary (. or ،)
                                                                     │
                                                                     └── greedy merge until 250-word limit
```

After splitting, a **30-word sliding overlap** is prepended to each chunk (except the first) from the tail of the previous chunk.  This preserves context continuity for embedding models.

---

## Tuning Parameters

| Parameter | Value | Description |
|---|---|---|
| `CHUNK_WORD_LIMIT` | 250 | Target max words per chunk |
| `OVERLAP_WORDS` | 30 | Words of overlap carried into the next chunk |

These constants are defined at the top of `pipeline/stage_3_7_chunk.py` and can be adjusted per-law if needed.

---

## Chunk Schema

Each item in `chunks.json` contains:

```json
{
  "chunk_id":        "EG_PDPL_008_C001",
  "article_id":      "EG_PDPL_008",
  "law_id":          "EG_PDPL",
  "chunk_index":     0,
  "chunk_total":     3,
  "text":            "...",
  "word_count":      248,
  "char_count":      1420,
  "has_overlap":     false,
  "article_number":  1,
  "article_type":    "main",
  "article_category": "تعريف",
  "topic":           "التعريفات والمصطلحات القانونية",
  "keywords":        ["البيانات الشخصية", "المعالجة", "المتحكم"],
  "legal_entities":  ["المركز"],
  "is_repealed":     false
}
```

**Metadata is denormalised** onto each chunk (topic, keywords, legal_entities, etc.) so downstream retrieval never needs to join back to the article layer.

---

## Pilot Results (EG_PDPL)

| Metric | Value |
|---|---|
| Total articles | 56 |
| Total chunks | 61 |
| Single-chunk articles | 52 (93%) |
| Multi-chunk articles | 4 (7%) |
| Avg words / chunk | 97.5 |
| Max words / chunk | 267 |
| Min words / chunk | 11 |

The 4 multi-chunk articles are the legitimately long ones identified in Stage 2.5 (definitions article Art.1 = 660 words, rights articles, penalties).

---

## Chunk ID Convention

```
{LAW_ID}_{SEQ:03d}_C{CHUNK_INDEX:03d}

EG_PDPL_008_C001   ← Article EG_PDPL_008, chunk 1
EG_PDPL_008_C002   ← Article EG_PDPL_008, chunk 2 (has_overlap=true)
EG_PDPL_009_C001   ← Next article, single chunk
```

---

## Why This Approach?

- **Paragraph-first**: Arabic legal text uses double newlines between logical paragraphs (numbered clauses, sub-items).  Splitting there preserves meaning better than character/token slicing.
- **Sentence fallback**: When a paragraph is very dense (definitions lists), sentence boundaries (`،` / `.`) provide a finer split.
- **Overlap window**: Ensures embedding models have context at chunk boundaries — useful when a sentence continues a thought from the previous chunk.
- **No LLM calls**: Pure Python — zero API cost.

---

## Running Stage 3.7

Stage 3.7 runs automatically as part of the full pilot:

```bash
python run_pilot.py EG_PDPL
```

To run Stage 3.7 alone:

```python
from config.law_registry import get_law
from pipeline import stage_3_7_chunk

law = get_law("EG_PDPL")
report = stage_3_7_chunk.run(law_entry=law)
print(f"Chunks: {report.total_chunks} | Avg words: {report.avg_chunk_words}")
```
