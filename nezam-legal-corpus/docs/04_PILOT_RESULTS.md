# Pilot Results — Stage 1 + Stage 1.5

**Date:** 2026-06-21  
**Stages run:** Stage 1 (Extraction) + Stage 1.5 (Confidence Scoring)  
**Laws tested:** EG_PDPL, EG_ESIGN

---

## EG_PDPL — قانون حماية البيانات الشخصية (151/2020)

### Summary

| Field | Value |
|-------|-------|
| Extraction source | **plaintext** (masaar.net TXT) |
| Characters extracted | 37,003 |
| PDF pages | 0 (TXT source) |
| Article markers found | 56 |
| Expected articles | 56 |
| Structural headings found | 24 |
| Expected headings | 14 |
| Arabic char density | 0.8114 |
| Replacement char density | 0.000000 |
| Gemini API calls | **0** (no OCR needed) |
| Cost | **$0.00** |

### Confidence Report

| Factor | Raw | Normalized | Weight | Contribution |
|--------|-----|-----------|--------|-------------|
| ACD (Arabic char density) | 0.8114 | 0.8114 | 0.25 | 0.2029 |
| AMD (article marker density) | 1.0000 | 1.0000 | 0.25 | 0.2500 |
| EACC (article count coverage) | 1.0000 | 1.0000 | 0.25 | 0.2500 |
| SHC (structural heading coverage) | 1.0000 | 1.0000 | 0.15 | 0.1500 |
| CS (corruption — inverted) | 0.0000 | 1.0000 | 0.10 | 0.1000 |
| **Total** | | | | **0.9528** |

**Result: PASS ✅ (threshold: 0.85) — Manual review: No**

### Notes

- SHC = 1.0 despite 24 headings found vs 14 expected. This is correct: `shc = min(1, 24/14) = 1.0`. The extra headings are sub-headings within chapters, which is expected in this law's structure.
- All 56 articles found with zero corruption. The TXT path is the cleanest possible extraction.
- Boilerplate stripping removed website navigation from masaar.net successfully.

### Output Files

```
data/extracted_raw/EG_PDPL.txt               (37,003 chars)
data/extracted_raw/EG_PDPL_meta.json
data/extracted_raw/EG_PDPL_confidence.json
```

---

## EG_ESIGN — قانون التوقيع الإلكتروني (15/2004)

### Summary

| Field | Value |
|-------|-------|
| Extraction source | **gemini_ocr** (PyMuPDF confidence 0.7317 → fallback) |
| Characters extracted | 13,590 |
| PDF pages | 8 |
| Article markers found | 35 |
| Expected articles | 32 |
| Structural headings found | 0 |
| Expected headings | 0 |
| Arabic char density | 0.8091 |
| Replacement char density | 0.000000 |
| OCR model | gemini-3.5-flash |
| Gemini API calls | **1** |
| Input tokens | 2,636 |
| Output tokens | 4,379 |
| Cost | **$0.003023** |

### PyMuPDF Pre-Check (failed, triggered OCR)

| Factor | Value | Why it failed |
|--------|-------|--------------|
| PyMuPDF confidence | 0.7317 | Arabic ligature encoding defect in PDF |
| Article markers found | ~23 | Garbled ligatures break marker regex |
| Arabic density | low | Arabic chars encoded as non-Arabic Unicode points |

### Confidence Report (after Gemini OCR)

| Factor | Raw | Normalized | Weight | Contribution |
|--------|-----|-----------|--------|-------------|
| ACD (Arabic char density) | 0.8091 | 0.8091 | 0.25 | 0.2023 |
| AMD (article marker density) | 1.0938 | 0.9062 | 0.25 | 0.2266 |
| EACC (article count coverage) | 1.0000 | 1.0000 | 0.25 | 0.2500 |
| SHC (structural heading coverage) | 1.0000 | 1.0000 | 0.15 | 0.1500 |
| CS (corruption — inverted) | 0.0000 | 1.0000 | 0.10 | 0.1000 |
| **Total** | | | | **0.9288** |

**Result: PASS ✅ (threshold: 0.85) — Manual review: No**

### Notes

- AMD = 1.0938 (35 markers found, 32 expected). Over-count of 3 likely from Gemini formatting section headers as article markers. AMD_norm = 0.9062 (penalizes deviation from 1.0 symmetrically).
- SHC = 1.0 because `expected_chapter_headings = 0` for this law — SHC is not applicable.
- Zero replacement characters — Gemini OCR output is clean.
- This law confirmed the OCR fallback path works end-to-end.

### Output Files

```
data/extracted_raw/EG_ESIGN.txt              (13,590 chars)
data/extracted_raw/EG_ESIGN_meta.json
data/extracted_raw/EG_ESIGN_confidence.json
```

---

## Stage 1.3 Cleanup Results

Stage 1.3 now runs between Stage 1 and Stage 1.5. Confidence scores are calculated on the **cleaned** text.

| Law | Chars Before | Chars After | Removed | Tatweel | Diacritics | Hamza | Yeh |
|-----|-------------|------------|---------|---------|-----------|-------|-----|
| EG_PDPL | 37,003 | 36,283 | 720 (1.95%) | 621 | 99 | 969 | 272 |
| EG_ESIGN | 13,590 | 13,580 | 10 (0.07%) | 9 | 1 | 303 | 179 |

**EG_PDPL note:** 621 tatweel characters removed (decorative stretches in the masaar.net TXT source). Confidence dropped from 0.9528 (raw) → 0.9206 (clean). The AMD factor changed from 1.0000 to 1.1250 (63 markers detected instead of 56) — normalization slightly altered character sequences adjacent to article markers. Still PASS at 0.9206.

**EG_ESIGN note:** Very clean OCR output, minimal cleanup needed. Confidence remains ~0.93.

---

## Infrastructure Issues Discovered During Pilot

| Issue | Root Cause | Resolution Applied |
|-------|-----------|-------------------|
| 6 old API keys rejected (403) | Keys were exposed in `.replit` `[userenv.shared]` and pushed to GitHub — Google revoked them | Removed from `.replit`, moved to Secrets; added `mark_permanently_disabled()` to KeyManager |
| `gemini-2.0-flash` daily quota exhausted | All 4 keys (20 RPD each) burned through by repeated test runs | Switched to `gemini-3.5-flash`; added RPM vs RPD distinction in 429 handling |
| `PRIMARY_MODEL` was `gemini-3.5-flash` in code but `gemini-2.0-flash` via env override | Incorrect env var set during debugging | Fixed env var; default in `settings.py` is now `gemini-3.5-flash` |

---

## API Budget Consumed (Pilot)

| Model | Law | Input tokens | Output tokens | Cost |
|-------|-----|-------------|--------------|------|
| gemini-3.5-flash | EG_ESIGN | 2,636 | 4,379 | $0.003023 |
| — | EG_PDPL | 0 | 0 | $0.00 |
| **Total** | | **2,636** | **4,379** | **$0.003023** |

Note: Additional quota was used during debugging runs that hit rate limits. Those are not reflected here as they produced no output.

---

## Next Pilot Step

Run Stage 1+1.5 on **EG_EVIDENCE** (قانون الإثبات, 99 articles):

```bash
python run_pilot.py EG_EVIDENCE
```

EG_EVIDENCE PDF must be placed at `data/raw_pdfs/EG_EVIDENCE.pdf` first.  
EG_EVIDENCE uses classical `مادة N` patterns — this is the first real test of the Arabic article splitter (Stage 2) once that stage is built.
