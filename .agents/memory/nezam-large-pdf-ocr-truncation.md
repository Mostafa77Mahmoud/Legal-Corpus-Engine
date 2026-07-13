---
name: Nezam large-PDF OCR truncation bug
description: Gemini OCR (ocr_pdf in utils/llm_client.py) can silently stop after a few pages on long scanned PDFs, with no truncation detection in the pipeline.
---

Gemini OCR verbatim-extraction calls (`ocr_pdf`) request `max_output_tokens=65536` and just
return `response.text` — there is no check of `response.candidates[0].finish_reason` and no
page-range chunking/continuation loop. On a 104-page scanned PDF (EG_COMMERCIAL, ~700 expected
articles) the model stopped after only ~4 pages (6.7k chars), producing a low Stage-1.5
confidence fail instead of an error — easy to mistake for a normal "needs manual review" case
rather than a systemic extraction failure.

**Why:** confirmed by comparing extracted char count/page count against the source page count
(104 pages → only 4 pages worth of text) and reading `ocr_pdf`: no finish_reason check, no
chunking for very long documents. Existing duplicate-detection logic (`_detect_and_strip_full_duplication`)
only covers the opposite failure mode (double-emission), not early stopping.

**How to apply:** before trusting a Gemini-OCR extraction for any large (~50+ page) scanned PDF,
compare extracted char count to page count as a sanity check (rough heuristic: legal Arabic text
is roughly 300-600 chars/page). If it looks too small, treat it as a truncation failure, not a
quality/confidence issue — do not just lower the confidence threshold. Fixing this needs either
page-range chunking of the OCR call or finish_reason-aware retry, which is a real code change —
get user sign-off before implementing (per this project's no-unrequested-debugging rule).
