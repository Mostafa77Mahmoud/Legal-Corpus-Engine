---
name: Stage-2 regex patterns
description: Article marker regex patterns for stage_2_split.py — critical anchoring and markdown rules
---

## Rule
`_MD = r"^#{0,6}[ \t]*"` — optional Markdown heading prefix for article markers.

**Why:** Gemini OCR wraps markers in `### مادة ١` headings. Plain-text laws use bare `مادة ١`. The prefix handles both. Use `[ \t]*` (not `\s*`) to avoid consuming newlines, which corrupts split boundaries.

**How to apply:**
- All three patterns (_ISSUANCE_RE, _PAREN_DIGIT_RE, _PRIMARY_RE) use `rf"{_MD}..."` 
- `re.MULTILINE` flag required on all patterns — `^` must anchor to line start
- `\s*` in _MD causes matches to include leading `\n` in double-newline gaps → split boundary shifts by 1 → issuance articles disappear from output
- Test with: `_ISSUANCE_RE.finditer(text)` — check that matches do NOT start with `\n`
