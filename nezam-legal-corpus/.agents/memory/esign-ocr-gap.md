---
name: EG_ESIGN OCR gap
description: Gemini OCR of EG_ESIGN misses the 2 issuance articles from the law's preamble
---

## Known gap
EG_ESIGN (Electronic Signature Law 15/2004) has:
- 2 issuance articles: `(المادة الأولى)` and `(المادة الثانية)` in the preamble
- 30 main law articles: مادة ١ … مادة ٣٠
- Total in original: 32

Gemini OCR output starts directly at `### مادة ١` — the 2 issuance articles are absent.

**Fix applied:** `law_registry.py` sets `expected_article_count=30` for EG_ESIGN (not 32). The notes field documents the gap.

**Why not fix the OCR?** The issuance articles are administrative boilerplate. The 30 main articles contain the substantive law. Acceptable gap for pilot phase.
