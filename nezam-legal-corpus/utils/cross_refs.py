"""
utils/cross_refs.py — Explicit Cross-Reference Extractor
==========================================================
Regex-based extractor for inter-article references in Egyptian legal texts.
Zero API cost — pure Python.

Patterns handled
────────────────
• المادة (٥)                     → [5]
• المادة (14)                    → [14]
• المادتين (١٩)، (٢٢)          → [19, 22]
• المادتين (19) و(21)           → [19, 21]
• المادة (٥) البند (ج) من المادة (٩)  → [5, 9]
• المواد (5) و(6) و(7)          → [5, 6, 7]
• المادة 147                     → [147]  (no parens variant)
• المادتين السابقتين             → relative (skipped, no number)
• المادة التالية                 → relative (skipped, no number)

Digit forms
───────────
Arabic-Indic (٠١٢٣٤٥٦٧٨٩) and Western (0-9) are both normalised to int.

Output schema per reference
───────────────────────────
{
    "ref_text":      "المادتين (١٩)، (٢٢)",   # verbatim matched text
    "article_numbers": [19, 22],               # parsed article numbers (int list)
    "same_law":      true                      # always true (cross-law refs not supported)
}
"""

from __future__ import annotations

import re
from typing import Any

# ── Arabic-Indic digit → ASCII digit mapping ──────────────────────────────────

_AR_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# digit group: matches Western or Arabic-Indic digits (1-4 digits)
_DG = r"[\u0660-\u0669\u06F0-\u06F90-9]{1,4}"

# ── Core patterns ─────────────────────────────────────────────────────────────
# Each pattern captures the full reference span and yields article numbers
# via named groups.  We compile them once at module load.

_PATTERNS: list[re.Pattern] = [

    # المادتين (19) و(21)  /  المادتين (١٩)، (٢٢)
    re.compile(
        r"المادتين\s*\(?(" + _DG + r")\)?\s*[،,و\s]+\(?(" + _DG + r")\)?",
        re.UNICODE,
    ),

    # المواد (5) و(6) و(7)
    re.compile(
        r"المواد\s*(?:\(?" + _DG + r"\)?\s*[،,و\s]+)*\(?(" + _DG + r")\)?",
        re.UNICODE,
    ),

    # البند (...) من المادة (4)
    re.compile(
        r"البند\s*\([^)]+\)\s*من\s*المادة\s*\(?(" + _DG + r")\)?",
        re.UNICODE,
    ),

    # المادة (٥)  /  المادة 147
    re.compile(
        r"المادة\s*\(?(" + _DG + r")\)?",
        re.UNICODE,
    ),
]

# Full-span pattern: captures the entire reference phrase for ref_text
_SPAN_PATTERN = re.compile(
    r"(?:المواد|المادتين|المادة)\s*(?:البند\s*\([^)]+\)\s*من\s*المادة\s*)?\(?(?:"
    + _DG + r")?\)?(?:\s*[،,و\s]+\(?(?:" + _DG + r")?\)?)*",
    re.UNICODE,
)


# ── Public API ────────────────────────────────────────────────────────────────

def extract_cross_refs(text: str) -> list[dict[str, Any]]:
    """
    Extract explicit cross-references from an Arabic legal article text.

    Parameters
    ----------
    text : str
        The cleaned article text.

    Returns
    -------
    list of dicts, each with keys:
        ref_text      : str        verbatim matched phrase
        article_numbers: list[int] referenced article numbers
        same_law      : bool       always True (inter-law refs not supported)

    De-duplicated: the same (article_number) will not appear twice.
    """
    results: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()

    for span_match in _SPAN_PATTERN.finditer(text):
        span_text = span_match.group()

        # Skip relative references (no digits found in span)
        digits_in_span = re.findall(_DG, span_text)
        if not digits_in_span:
            continue

        # Parse article numbers from this span
        numbers: list[int] = []
        for raw_dig in digits_in_span:
            # Normalise Arabic-Indic → ASCII
            normalised = raw_dig.translate(_AR_INDIC)
            try:
                n = int(normalised)
                if 1 <= n <= 9999 and n not in seen_numbers:
                    numbers.append(n)
                    seen_numbers.add(n)
            except ValueError:
                continue

        if numbers:
            # Clean ref_text: collapse internal whitespace/newlines, trim trailing punctuation
            clean_ref = re.sub(r"\s+", " ", span_text).strip()
            clean_ref = re.sub(r"[\s،,وأو]+$", "", clean_ref).strip()
            results.append({
                "ref_text":        clean_ref,
                "article_numbers": numbers,
                "same_law":        True,
            })

    return results


def enrich_with_cross_refs(article: dict[str, Any]) -> dict[str, Any]:
    """
    Convenience wrapper: adds/overwrites `explicit_cross_refs` on an article dict.
    Returns the article dict (mutated in-place AND returned for chaining).
    """
    article["explicit_cross_refs"] = extract_cross_refs(article.get("text", ""))
    return article
