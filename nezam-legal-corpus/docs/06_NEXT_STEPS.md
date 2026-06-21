# Next Steps — What to Build Next

**Current state:** Stage 1 + Stage 1.5 complete and pilot-tested. Ready to move to Stage 1.3.

---

## Immediate Next Step: Stage 1.3 — Arabic Cleanup

**Why first?** Stage 1.3 sits between Stage 1 and Stage 1.5 in the final pipeline. Right now `run_pilot.py` goes 1 → 1.5 directly. Insert 1.3 between them before building Stage 2.

### What to build in `pipeline/stage_1_3_cleanup.py`

```python
def run(law_entry: LawEntry) -> CleanupResult:
    """
    Input:  data/extracted_raw/{LAW_ID}.txt
    Output: data/extracted_clean/{LAW_ID}.txt
            data/cleanup_audit_logs/{LAW_ID}_diff.json
    """
```

**Transformations (use `utils/arabic_text.normalize()`):**
1. NFC Unicode normalization
2. Remove tatweel (U+0640)
3. Remove diacritics (U+064B–U+065F, U+0670)
4. Normalize Hamza variants → `ا`
5. Normalize Yeh variants → `ي`
6. Collapse multiple spaces → single space
7. Collapse 3+ newlines → double newline
8. Strip control characters (U+0000–U+0008, etc.)

**Audit log format:**
```json
{
  "law_id": "EG_PDPL",
  "total_chars_before": 37003,
  "total_chars_after": 36800,
  "changes": [
    {"type": "diacritic_removed", "count": 145},
    {"type": "tatweel_removed", "count": 12},
    {"type": "hamza_normalized", "count": 203}
  ],
  "cleaned_at": "2026-06-21T..."
}
```

**Update `run_pilot.py`** to call Stage 1.3 between Stage 1 and 1.5, and update Stage 1.5 to read from `extracted_clean/` instead of `extracted_raw/`.

---

## After Stage 1.3: Stage 2 — Article Splitting

This is the most complex stage. Build it after Stage 1.3 is verified on both EG_PDPL and EG_ESIGN.

### Key decisions already made

1. **Regex-first:** Attempt splitting with regex before any Gemini call.
2. **LLM fallback:** Only call Gemini on sections that fail Stage 2.5 validation.
3. **No guessing:** If both regex and LLM fail a section, flag for human review — do not silently produce bad splits.

### Regex patterns needed (priority order)

```python
# 1. Ordinal issuance articles: (المادة الأولى) ... (المادة السابعة)
ISSUANCE_MARKER = re.compile(r"\(المادة\s+(الأول[ىة]?|الثاني[ة]?|...)\)")

# 2. Standard: مادة (٥) or مادة (5)
PAREN_DIGIT = re.compile(r"مادة\s*\(\s*(?:\d+|[٠-٩]+)\s*\)")

# 3. Standard: مادة 5 or المادة 5
PRIMARY = re.compile(r"(?:مادة|المادة)\s+(?:\d+|[٠-٩]+)")
```

These patterns are already in `utils/arabic_text.py` for counting — reuse them as split boundaries.

### Output schema

```json
{
  "article_id": "EG_PDPL_001",
  "law_id": "EG_PDPL",
  "article_number": 1,
  "article_number_text": "الأولى",
  "article_type": "issuance",
  "raw_text": "...",
  "clean_text": "...",
  "is_repealed": false,
  "sequence_index": 1,
  "split_source": "regex",
  "word_count": 45,
  "char_count": 280
}
```

---

## Full Build Order Recommendation

```
1. Stage 1.3  — Arabic Cleanup                    (~2 hours)
2. Stage 2    — Article Splitting (regex)          (~4 hours)
3. Stage 2.5  — Split Validation                   (~2 hours)
4. Run full Stage 1→2.5 on EG_PDPL, verify output  (~1 hour)
5. Run on EG_EVIDENCE                              (~30 min)
6. Manually inspect EG_EVIDENCE output             (~1 day)
7. Build golden benchmark from EG_EVIDENCE         (~2 hours)
8. Stage 3    — Metadata Enrichment               (~4 hours)
9. Stage 3.7  — Chunking                           (~2 hours)
10. Stage 4   — Human Review Export               (~2 hours)
11. Stage 5   — Validation                         (~2 hours)
12. Stage 6   — Assembly                           (~1 hour)
13. Stage 7   — MongoDB + JSON Export             (~2 hours)
14. Post      — Embeddings script                  (~1 hour)
```

---

## Stage 2 LLM Fallback Prompt (Draft)

When regex splitting fails on a section, send this to Gemini:

```
أنت نظام متخصص في تحليل النصوص القانونية المصرية.

النص التالي من قانون {law_name}. قم بتحديد حدود كل مادة وأعد النص مقسماً 
بوضوح مع الإشارة لرقم كل مادة.

القواعد:
- لا تعدل النص أو تحذف أي كلمة
- ضع علامة [مادة N] قبل كل مادة
- إذا كانت المادة ملغاة، أشر بـ [ملغاة]
- أعد النص كاملاً بدون حذف

النص:
{text_section}
```

---

## API Cost Projection for Remaining Stages

| Stage | Model | Calls per law (avg) | Cost per law (est.) |
|-------|-------|--------------------|--------------------|
| 1 (OCR) | gemini-3.5-flash | 0–1 | $0–$0.01 |
| 2 (LLM fallback) | gemini-3.5-flash | 0–5 | $0–$0.05 |
| 3 (Enrichment) | gemini-3.5-flash | 1 per article | $0.05–$2.00 |
| Post (Embeddings) | text-embedding-004 | 1 per chunk | ~$0.001 |

**Stage 3 is the biggest cost driver.** EG_CIVIL_CODE (686 articles) ≈ 686 calls ≈ 9 days at 80 RPD/day with 4 free-tier keys. Consider upgrading to paid tier for large laws or batching enrichment across multiple days.

---

## MongoDB Setup (For Stage 7)

Not yet configured. When ready:
1. Set `MONGODB_URI` in Replit Secrets
2. Create database `nezam_corpus`
3. Collections will be created automatically by Stage 7:
   - `egyptian_law_articles`
   - `egyptian_law_chunks`

Indexes needed:
```javascript
// egyptian_law_articles
db.egyptian_law_articles.createIndex({ law_id: 1, article_number: 1 }, { unique: true })
db.egyptian_law_articles.createIndex({ legal_concepts: 1 })

// egyptian_law_chunks
db.egyptian_law_chunks.createIndex({ article_id: 1 })
db.egyptian_law_chunks.createIndex({ law_id: 1 })
```
