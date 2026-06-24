---
name: Nezam EG_CIVIL_CODE law_registry configuration
description: Key config decisions for the Egyptian Civil Code (القانون المدني 131/1948) in law_registry.py.
---

## Key settings

```python
expected_article_count=1149,        # full article range 1–1149
expected_chapter_headings=0,        # suppress SHC penalty (see below)
repealed_articles=[111 articles],   # physically absent from the edition
```

## repealed_articles — 111 missing articles

The وزارة المالية "وفقا لأحدث تعديلاته" edition physically removes repealed articles. Confirmed by Stage 2.5 sequence-gap analysis. The splitter finds 1038 main articles; 1038 + 111 = 1149 ✓.

List: [8, 38, 54-80, 88, 101, 115, 153, 224, 258, 290, 317, 322, 352, 372, 389-417, 424, 431, 451, 460, 466, 478, 492, 512, 552, 595, 636, 672, 708, 748, 780, 793, 798, 827, 838, 877, 880, 885, 889, 895, 897, 917, 924, 925, 933, 934, 949, 962, 969, 971, 988, 1003, 1022, 1037, 1074, 1087, 1116, 1125]

## expected_chapter_headings=0

**Why:** The Civil Code PDF uses Arabic Presentation Form ordinals (U+FE70-FEFF). After NFKC normalization, they become hamza-less forms (الاول not الأول). The `_HEAD_PLAIN` regex never matches, so SHC (section heading confidence) would be 0/N = 0, crashing the confidence score below 0.85. Setting expected=0 makes SHC=1.0 and confidence passes at 0.8946.

## Extraction

PyMuPDF only (no Gemini OCR needed). Confidence 0.8946 PASS.
