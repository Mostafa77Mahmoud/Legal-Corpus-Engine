# Pipeline Roadmap — Full 9-Stage Plan

**Architecture decision document:** See `attached_assets/Pasted-Here-is-the-final-production-readiness-review-V7-Produc_1782052619933.txt`

---

## Stage Status Overview

| Stage | Name | Status | File |
|-------|------|--------|------|
| **1** | Raw Extraction | ✅ **DONE** | `pipeline/stage_1_extract.py` |
| **1.3** | Arabic Cleanup | ❌ Not built | `pipeline/stage_1_3_cleanup.py` |
| **1.5** | Confidence Scoring | ✅ **DONE** | `pipeline/stage_1_5_val_extract.py` |
| **2** | Article Splitting | ❌ Not built | `pipeline/stage_2_split.py` |
| **2.5** | Split Validation | ❌ Not built | `pipeline/stage_2_5_val_split.py` |
| **3** | Metadata Enrichment | ❌ Not built | `pipeline/stage_3_enrich.py` |
| **3.7** | Chunking | ❌ Not built | `pipeline/stage_3_7_chunks.py` |
| **4** | Human Review Export | ❌ Not built | `pipeline/stage_4_review.py` |
| **5** | Rule-Based Validation | ❌ Not built | `pipeline/stage_5_validate.py` |
| **6** | Assembly | ❌ Not built | `pipeline/stage_6_assemble.py` |
| **7** | MongoDB + JSON Export | ❌ Not built | `pipeline/stage_7_export.py` |
| **Post** | Embeddings (separate) | ❌ Not built | `scripts/generate_embeddings.py` |

---

## Stage Specifications

### Stage 1.3 — Arabic Cleanup *(next to build)*

**Input:** `data/extracted_raw/{LAW_ID}.txt`  
**Output:** `data/extracted_clean/{LAW_ID}.txt` + `data/cleanup_audit_logs/{LAW_ID}_diff.json`

**What it does:**
- Unicode normalization (NFC) via `utils/arabic_text.normalize()`
- Remove tatweel (U+0640) and diacritics
- Normalize Hamza, Yeh, Heh variants
- Collapse multiple spaces/newlines
- Strip control characters
- Log a character-level diff so reviewers can see exactly what changed

**Key constraint:** The audit log must capture every change. No silent modifications.

---

### Stage 2 — Article Splitting

**Input:** `data/extracted_clean/{LAW_ID}.txt`  
**Output:** `data/split_articles/{LAW_ID}/articles.json`

**Strategy: Regex-first → LLM fallback**

```
For each law text:
1. Apply regex patterns to locate all article boundaries
2. Run Stage 2.5 validation on the split result
3. If validation PASS → done
4. If validation FAIL → pass problematic sections to Gemini for re-splitting
5. Re-validate → if still FAIL → flag for human review
```

**Article patterns to handle:**
- `مادة (١)` / `مادة 1` / `المادة الأولى`
- Issuance articles: `(المادة الأولى)` ... `(المادة السابعة)`
- Numbered sub-clauses: `أولاً`, `ثانياً`, `١-`, `(أ)`, `(ب)`

**Each article record:**
```json
{
  "article_id": "EG_PDPL_001",
  "law_id": "EG_PDPL",
  "article_number": 1,
  "article_number_text": "الأولى",
  "article_type": "issuance",
  "raw_text": "...",
  "is_repealed": false,
  "sequence_index": 1
}
```

---

### Stage 2.5 — Split Validation

**6-code error taxonomy:**

| Code | Name | Description |
|------|------|-------------|
| `E001` | MISSING_ARTICLE | Article number in sequence not found |
| `E002` | DUPLICATE_ARTICLE | Same article number appears twice |
| `E003` | SEQUENCE_GAP | Non-consecutive article numbers without registered repeal |
| `E004` | EMPTY_BODY | Article has number marker but no body text |
| `E005` | OVERSIZED_ARTICLE | Article text > 3× median (possible failed split) |
| `E006` | ORPHAN_TEXT | Text between articles not assigned to any article |

**Continuity check:** Every article from 1 to `expected_article_count` must be present unless listed in `law_entry.repealed_articles`.

**Quality gate:** Zero E001, E002, E004 errors. E003/E005/E006 allowed only if flagged and under threshold.

---

### Stage 3 — Metadata Enrichment

**Input:** `data/split_articles/{LAW_ID}/articles.json`  
**Output:** `data/enriched/{LAW_ID}/articles_enriched.json`

**Per-article enrichment via Gemini:**
- `keywords`: list of Arabic legal terms
- `legal_concepts`: matched against `config/taxonomy.py`
- `explicit_cross_refs`: list of `{"target_law": "EG_PDPL", "target_article": 5, "ref_text": "..."}`
- `article_type`: `"definition"` | `"obligation"` | `"penalty"` | `"procedural"` | `"issuance"` | `"general"`
- `summary_ar`: one-sentence Arabic summary (optional)

**Cost note:** This is the most expensive stage. Each article = 1 Gemini call. EG_PDPL (56 articles) ≈ 56 calls. At 20 RPD per key × 4 keys = 80 calls/day, small laws can be enriched in 1 day.

---

### Stage 3.7 — Chunking

**Input:** `data/enriched/{LAW_ID}/articles_enriched.json`  
**Output:** `data/chunks/{LAW_ID}/chunks.json`

**Strategy: Paragraph-first, 500-token ceiling, no overlap**

```
For each article:
1. Split on \n\n paragraph boundaries
2. Merge consecutive paragraphs until approaching 400 tokens
   (100-token headroom before ceiling)
3. If single paragraph > 500 tokens:
   → Split at Arabic sentence boundary (. or ؛)
   → Never split mid-sentence
4. Each chunk gets: chunk_id, article_id, paragraph_index, token_count
```

**Why no overlap?** Article-level legal text is short enough that the parent article is always fetched for LLM reasoning. Overlap multiplies embedding storage without retrieval quality gain.

---

### Stage 4 — Human Review Export

**Input:** `data/enriched/` + `data/chunks/`  
**Output:** `data/review_queue_{LAW_ID}.json`

Articles sorted by descending priority score (based on: validation errors, low confidence, OCR source, cross-reference count). Reviewers see the most problematic articles first.

---

### Stage 5 — Validation

**Rule-based checks** for all articles + **LLM re-validation** only for `ERROR_INVALID_REFERENCE` cases.

Cross-reference validation: every explicit_cross_ref must resolve to a known law ID and article number in the registry.

---

### Stage 6 — Assembly

- Deduplication across laws
- `is_current_version` resolution (if multiple versions of a law exist)
- `is_repealed` flag propagation from `law_registry.repealed_articles`

---

### Stage 7 — MongoDB + JSON Export

**Collections:**
```
egyptian_law_articles   — one document per article
egyptian_law_chunks     — one document per chunk
```

**Also produces:** `data/releases/{LAW_ID}/release_metadata.json` with article count, chunk count, extraction sources, confidence scores, processing date.

---

## Pilot Execution Order

Ordered for fast feedback → progressive difficulty:

| # | Law ID | Articles | PDF Type | Why This Slot |
|---|--------|----------|---------|---------------|
| 1 | EG_PDPL | 56 | Digital + TXT | ✅ Done — baseline validation |
| 2 | EG_EVIDENCE | 99 | Mixed | Build golden benchmark here (classical مادة style) |
| 3 | EG_ESIGN | 32 | Digital (OCR) | ✅ Done — cross-ref test with PDPL |
| 4 | EG_LABOR | 254 | Mixed | First medium law, stress-tests paragraph chunking |
| 5 | EG_RENT | 80 | Scanned | First scanned PDF — validates OCR cost model |
| 6 | EG_CIVIL_PROCEDURE | 480+ | Mixed | First large law |
| 7 | EG_COMMERCIAL | 700 | Mixed | Core Nezam law |
| 8 | EG_CIVIL_CODE | 686 | Mixed (1948) | Most important — run only after 4+ laws succeed |
| 9 | EG_PENAL | 535+ | Scanned | Heavy OCR load — run late |
| 10 | EG_IP | 188 | Digital | Lowest priority |

**Critical rule:** Do not run EG_CIVIL_CODE until at least 4 laws have passed including one scanned law (EG_RENT, slot 5).

---

## Golden Benchmark (Build After First Full-Pipeline Pilot)

Build after the full pipeline (Stages 1–7) succeeds on EG_EVIDENCE:

1. Manually inspect ~100 articles from EG_EVIDENCE output
2. Select 50 representative articles covering: short articles, long articles with sub-clauses, articles with explicit cross-references, articles adjacent to sequence gaps
3. Verify each manually → freeze as `tests/golden/EG_EVIDENCE_golden.json`
4. Write `tests/test_golden_benchmark.py` against the frozen set
5. Gate: split F1 ≥ 0.98, explicit_refs F1 ≥ 0.95

**Do not build the golden benchmark before implementation.** The schema will change during the pilot run.

---

## Permanently Cut Features (V7 Decision)

These were in earlier versions and will **not** be reinstated:

- Amendment registry
- Temporal reconstruction
- Semantic relationship generation (Stage 3.5)
- Streamlit dashboard
- Retrieval evaluation framework
- Release receipt signing
- Runtime prompt hash verification
- Taxonomy drift migration
