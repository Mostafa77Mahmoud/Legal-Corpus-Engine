---
name: Stage-2.5 validation thresholds
description: Calibrated thresholds for the 6-code error taxonomy in stage_2_5_val_split.py
---

## Current thresholds (calibrated on EG_PDPL + EG_ESIGN)

| Code | Severity | Threshold | Reason |
|------|----------|-----------|--------|
| E005 OVERSIZED_ARTICLE | warning | 5× median, 500-word floor | PDPL Art.1 (definitions) = 660 words — legitimately large; 3× was too strict |
| E006 ORPHAN_TEXT | warning | > 800 chars | Preamble (Gazette header + law number) is always present; PDPL preamble = 239 chars |
| W003 ISSUANCE_MISMATCH | warning | ≥ 50 articles | Small/medium laws (<50 articles) often have no issuance section or OCR skips it |

**Why:** Egyptian legal texts have legitimately large articles (definitions, penalties, rights). The old 3× median ceiling was too tight for the PDPL. Raising to 5× + 500-word floor prevents false positives.

**How to apply:** E005 and E006 are warnings — they flag for human review, not pipeline failure. Only genuine split failures (articles merged, markers missed) should be errors.
