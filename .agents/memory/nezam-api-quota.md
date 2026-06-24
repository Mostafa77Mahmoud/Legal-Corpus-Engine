---
name: Nezam API quota and model switching
description: Gemini API key daily quota exhaustion pattern for the pipeline; how to resume Stage 3 enrichment after reset.
---

## Situation (as of 2026-06-24)

All 4 GEMINI_API_KEYS exhausted daily quota on both `gemini-3.5-flash` AND `gemini-2.5-flash` during Stage 3 enrichment of EG_CIVIL_CODE (1039 articles, ~104 batches of 10).

- `gemini-3.5-flash`: was returning 503 UNAVAILABLE (high demand) before quota exhaustion
- `gemini-2.5-flash`: completed 1 batch (10 articles) successfully then all keys hit 429 RPD

## Resume command (after UTC midnight key reset)

```bash
cd nezam-legal-corpus
PRIMARY_MODEL=gemini-2.5-flash python run_batch.py EG_CIVIL_CODE
```

**Why `PRIMARY_MODEL=...` prefix:** The Replit environment has `PRIMARY_MODEL=gemini-3.5-flash` set as a shell env var. `load_dotenv()` without `override=True` cannot override existing shell vars, so the `.env` file workaround doesn't work. The inline env var assignment is required.

## Stage 3 is resumable

Stage 3 caches each article individually after enrichment. On resume:
- EG_PDPL: 56/56 done ✓
- EG_ESIGN: 30/30 done ✓
- EG_CIVIL_CODE: 90/1039 done (80 pre-existing + 10 from last run); ~94 batches remaining

## Model notes

- `gemini-2.5-flash` uses `thinking_budget` (not `thinking_level`). Since `ENRICH_THINKING_LEVEL` is unset, no thinking config is sent — model works correctly.
- `gemini-2.5-flash` max output tokens: 65536 (same as configured `MAX_OUTPUT_TOKENS`).
