---
name: Nezam pipeline complete state
description: Current state of the Nezam Legal Corpus pipeline — what's done, what's next, key quirks for future sessions.
---

# Nezam Pipeline — Complete State

## What's built (as of June 2026)

All pipeline stages 1→7 are implemented and tested. Three laws are fully released:

| Law | Articles | Chunks | Production files |
|-----|----------|--------|-----------------|
| EG_PDPL | 56 | 61 | `data/releases/EG_PDPL/` |
| EG_ESIGN | 30 | 30 | `data/releases/EG_ESIGN/` |
| EG_CIVIL_CODE | 1039 | 1041 | `data/releases/EG_CIVIL_CODE/` |

## What's NOT built yet

- `scripts/generate_embeddings.py` — uses `text-embedding-004` model; input: `data/releases/{ID}/chunks.jsonl`
- MongoDB is optional (Stage 7 writes JSON/JSONL regardless; needs `MONGODB_URI` secret)

## Key dependency quirk

`requirements.txt` needs BOTH:
- `google-generativeai>=0.8.0` (used for `google.generativeai` in some utilities)
- `google-genai>=1.0.0` (used for `from google import genai` in `utils/llm_client.py`)

If `google-genai` is missing, Stage 1 and Stage 3 will fail at import time with `ImportError: cannot import name 'genai' from 'google'`. Fix: `pip install google-genai`.

**Why:** Both packages share the `google` namespace. They must both be installed.

## Stage 5 validation rules

V001 (ENRICHMENT_INCOMPLETE) and V003 (ENRICHMENT_ERROR) are blocking errors.
V002 (INVALID_CATEGORY), V004 (REPEALED_MISMATCH), V005 (EMPTY_KEYWORDS) are warnings only.

## Run command

```bash
cd nezam-legal-corpus
python run_batch.py EG_PDPL       # single law, full pipeline Stages 1→7
python run_batch.py               # default batch (EG_PDPL, EG_ESIGN, EG_CIVIL_CODE)
```

## Next law priority

EG_EVIDENCE (99 articles) — medium size, good for golden benchmark. See `docs/03_ROADMAP.md`.
