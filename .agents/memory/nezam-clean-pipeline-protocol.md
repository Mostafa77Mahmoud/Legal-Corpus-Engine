---
name: Nezam clean pipeline protocol
description: How to fully wipe a law's outputs before a clean re-run; covers old + new directory names and source file locations.
---

## Rule
Before re-running a law from Stage 1, delete ALL output directories using `find`, not a hand-coded list. A hand-coded list will miss directories.

## Correct cleanup command
```bash
find data -path "*<LAW_ID>*" -not -path "*/raw_pdfs/*" -delete
```

Then verify nothing remains:
```bash
find data -path "*<LAW_ID>*" -not -path "*/raw_pdfs/*" | sort
```

## Why
The pipeline has evolved and old runs produced data in legacy directory names that do not match the current structure:
- `data/enriched_articles/<LAW_ID>/`  ← old Stage 3 cache (now: `data/enriched/<LAW_ID>/`)
- `data/raw_txts/<LAW_ID>.txt`        ← old Stage 1 plaintext source (still used for txt_filename laws)
- `data/split_articles/<LAW_ID>/`     ← old Stage 2 output
- `data/validated/<LAW_ID>/`          ← old Stage 2.5 output

Deleting only `data/enriched/` and `data/articles/` etc. leaves the old-name directories intact, causing Stage 3 to load stale cached enrichments (with empty `enrichment_status`) instead of running fresh.

## Source file locations by law type
- **txt_filename laws** (e.g. EG_PDPL): Stage 1 reads from `data/raw_txts/<LAW_ID>.txt`. This file must NOT be deleted — restore from git if accidentally removed (`git show <commit>:nezam-legal-corpus/data/raw_txts/<ID>.txt > data/raw_txts/<ID>.txt`).
- **pdf_filename laws** (e.g. EG_ESIGN, EG_CIVIL_CODE): Stage 1 does OCR from `data/raw_pdfs/<LAW_ID>.pdf`. PDF must exist before running Stage 1.

## How to apply
Every time a "clean re-run" is requested:
1. Run the `find ... -delete` command above
2. Verify with a second `find` that nothing remains (except raw_pdfs)
3. Confirm the source file (txt or pdf) is in place
4. Then start the pipeline
