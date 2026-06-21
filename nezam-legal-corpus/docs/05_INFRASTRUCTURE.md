# Infrastructure Components

Documentation for the shared utilities used across all pipeline stages.

---

## 1. KeyManager — `utils/key_manager.py`

Thread-safe pool of Gemini API keys with automatic rotation and tiered cooldown.

### Key States

| State | `_rate_limited` | `_cooldown_end` | Recovers? |
|-------|----------------|----------------|-----------|
| Available | False | None | — |
| RPM-limited | True | `now + 65s` | Yes, after ~1 min |
| RPD-exhausted | True | `until UTC midnight` | Yes, next day |
| Permanently disabled | True | None | **Never** |

### Cooldown Logic

**RPM (per-minute limit):** Triggered by 429 responses containing "minute" in the message. Cooldown: 65 seconds (configurable via `KEY_RPM_COOLDOWN_SECONDS`).

**RPD (per-day limit):** Triggered by 429 responses containing "quota/billing/plan" but NOT "minute". Cooldown: until UTC midnight + 60s buffer (configurable via `KEY_RPD_COOLDOWN_SECONDS`, default 86400s).

**Permanent disable (403):** Triggered by HTTP 403 (leaked/revoked key). Key is removed from pool forever in this session. Logs `DISABLED` to `logs/key_rotation.log`.

### Blocking Behavior

`get_available_key_or_wait()` blocks until at least one key is available. If all keys are on RPD cooldown, it waits and polls every 30 seconds. It will never crash — it just waits.

### Key Rotation Log

All rotation events are written to `logs/key_rotation.log`:
```
2026-06-21 14:06:03  RPD_EXHAUSTED  ****n8w  cooldown=35157s (~9.8h)
2026-06-21 14:06:03  ROTATED  ****n8w -> ****soQ  reason=quota_exhausted_on_previous
```

### Rate Limits Per Key (Free Tier, as of 2026)

| Model | RPM | TPM | RPD |
|-------|-----|-----|-----|
| gemini-3.5-flash | 5 | 250K | 20 |
| gemini-2.5-flash | 5 | 250K | 20 |
| gemini-2.5-flash-lite | 10 | 250K | 20 |
| gemini-3.1-flash-lite | 15 | 250K | 500 |

With 4 keys: **80 OCR requests per day** on gemini-3.5-flash. One request per law for Stage 1; Stage 3 (metadata enrichment) will consume one request per article.

---

## 2. LLMClient — `utils/llm_client.py`

Wrapper around the `google-genai` SDK with retry logic, key rotation, and cost tracking.

### Two Functions

**`generate_text(prompt, ...)`**
Plain text generation. Used by future stages (Stage 2 LLM fallback, Stage 3 enrichment).

**`ocr_pdf(pdf_path, prompt, ...)`**
Upload PDF to Gemini File API, then generate. Implements key-pinning.

### Key-Pinning Rule (Critical)

Gemini File API files are scoped to the uploading API key's project. A file uploaded with key A **cannot** be accessed by key B. Therefore:

1. Pin one key for the entire upload+generate cycle.
2. If 429 occurs during generation → delete the uploaded file → rotate key → re-upload → retry.
3. If 403 occurs during upload → disable key permanently → rotate → retry upload.

This is implemented in `ocr_pdf()` via nested outer/inner loops.

### 429 Handling in `ocr_pdf`

```
Generation 429 received
    │
    ├─ Is it RPD? (message has "quota"/"billing")
    │   └─ YES: delete file, mark_daily_quota_exhausted, rotate, re-upload
    │
    └─ NO (it's RPM):
        ├─ gen_attempt 0 → wait 30s, retry same key
        ├─ gen_attempt 1 → wait 60s, retry same key
        └─ gen_attempt 2 → mark_rpm_limited (65s), rotate, re-upload
```

---

## 3. CostTracker — `utils/cost_tracker.py`

Tracks token usage and USD cost across all Gemini API calls in a pipeline run.

### Records

Each API call records:
- `stage`: which pipeline stage made the call
- `law_id`: which law was being processed
- `model`: model name
- `input_tokens` / `output_tokens`
- `cost_usd`: calculated from `GEMINI_FLASH_INPUT_COST_PER_1M` and `GEMINI_FLASH_OUTPUT_COST_PER_1M`
- `api_key_suffix`: last 3 chars of key used

Also records: key failures (`record_key_failure`) and rotations (`record_rotation`) for audit.

### Summary

`tracker.summary()` returns total calls, tokens, cost, and breakdown by stage.

---

## 4. ArabicText — `utils/arabic_text.py`

Domain-specific Arabic text utilities.

### Functions

| Function | Description |
|----------|-------------|
| `normalize(text)` | NFC, remove tatweel/diacritics, normalize hamza/yeh/heh variants, collapse whitespace |
| `arabic_char_density(text)` | Fraction of chars in Arabic Unicode ranges |
| `replacement_char_density(text)` | Fraction of U+FFFD replacement chars (OCR failure indicator) |
| `count_article_markers(text)` | Count unique article markers across all 4 format variants |
| `count_structural_headings(text)` | Count الباب/الفصل/القسم headings in plain and paren forms |
| `strip_txt_boilerplate(text)` | Crop masaar.net TXT to law content only |

### Arabic Unicode Ranges Covered

```python
_ARABIC_RANGE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
```

Covers: Basic Arabic, Arabic Supplement, Arabic Presentation Forms A & B.

### Normalization Applied

| Transform | Detail |
|-----------|--------|
| Hamza variants | `أإآاٱ` → `ا` |
| Yeh variants | `ىئ` → `ي` |
| Heh variants | `ةه` → `ه` |
| Tatweel | `ـ` removed |
| Diacritics | U+064B–U+065F, U+0670 removed |

---

## 5. Settings — `config/settings.py`

All pipeline constants in one file. Override any via environment variable.

| Variable | Default | Description |
|----------|---------|-------------|
| `PRIMARY_MODEL` | `gemini-3.5-flash` | OCR and LLM model |
| `EMBEDDING_MODEL` | `text-embedding-004` | Embedding model (post-export) |
| `CONFIDENCE_THRESHOLD` | `0.85` | Stage 1.5 quality gate |
| `PYMUPDF_MIN_CHARS` | `200` | Min chars for PyMuPDF to be scored |
| `KEY_RPM_COOLDOWN_SECONDS` | `65` | 429 RPM cooldown |
| `KEY_RPD_COOLDOWN_SECONDS` | `86400` | 429 RPD cooldown |
| `GEMINI_MAX_RETRIES` | `20` | Max retry attempts |
| `GEMINI_RETRY_BASE_DELAY` | `2.0` | Exponential backoff base (seconds) |
| `GEMINI_FLASH_INPUT_COST_PER_1M` | `0.25` | USD per 1M input tokens |
| `GEMINI_FLASH_OUTPUT_COST_PER_1M` | `1.00` | USD per 1M output tokens |

**Required secrets (set via Replit Secrets, never in `.replit`):**
- `GEMINI_API_KEYS` — comma-separated API keys

---

## 6. Law Registry — `config/law_registry.py`

Central registry of all 10 Egyptian laws. Each `LawEntry` contains:

```python
@dataclass
class LawEntry:
    law_id: str                        # "EG_PDPL"
    law_name_ar: str                   # "قانون حماية البيانات الشخصية"
    law_number: str                    # "151 لسنة 2020"
    year: int                          # 2020
    pdf_filename: str                  # "EG_PDPL.pdf"
    expected_article_count: int        # 56
    repealed_articles: list[int]       # [] or [12, 45, ...]
    expected_chapter_headings: int     # 14
    txt_filename: str | None           # "EG_PDPL.txt" or None
    notes: str                         # free-text notes for future agents
```

`txt_filename` takes priority over PDF in Stage 1. If both exist, TXT is used (no Gemini call needed).
