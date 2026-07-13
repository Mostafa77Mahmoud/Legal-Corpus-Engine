---
name: Nezam "مكررا" (bis) article splitting bug
description: The Stage 2 splitter has no concept of "المادة N مكررا" (bis/inserted) articles, causing duplicate article_number collisions and corrupted article boundaries.
---

Egyptian law amendments commonly insert a new article between existing ones without renumbering
the whole law, using the convention "المادة 148 مكررا" (Article 148 bis). `pipeline/stage_2_split.py`
has no handling for "مكرر" at all (confirmed via grep — zero matches). When such an article exists,
the splitter matches "مادة (148" for both the original Article 148 and "148 مكررا" as the same
`article_number=148`, producing:
- Stage 2.5 validation error E002 DUPLICATE_ARTICLE.
- A corrupted split: the qualifier word "مكررا" plus the following "):" get left behind as the
  start of the *next* article's text, and the original Article 148's own text gets truncated
  mid-sentence (its real ending is absorbed as filler for the bis article).

**Why:** found while onboarding EG_EVIDENCE (25/1968) after correcting its expected_article_count
(99 → 162, preliminary estimate): Stage 2.5 flagged `EG_EVIDENCE_150`/`EG_EVIDENCE_151` as duplicate
article 148, and inspecting the two article bodies showed the "مكررا):" fragment orphaned onto the
second one.

**How to apply:** treat any Stage 2.5 DUPLICATE_ARTICLE error as a possible bis-article case, not
just a genuine duplicate/OCR artifact — check the raw text around the marker for "مكرر"/"مكررا"/
"مكررة" before assuming it's a data problem. Fixing this generally needs splitter changes (e.g. a
distinct bis suffix in the marker regex/article_id, akin to "148_mkr" or "148B") — a real code
change requiring user sign-off before implementing (per this project's no-unrequested-debugging rule).
