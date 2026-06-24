"""
Stage 2: Article Splitting

Splits the cleaned law text into individual article records using a
regex-first strategy with a planned LLM fallback for problem sections.

Strategy
--------
1. Scan the cleaned text for all article-marker positions using three
   pattern families (ordered from most to least specific):
     a. Ordinal issuance articles  — (المادة الاولي) … (المادة السابعة)
     b. Paren-digit markers        — مادة (١) / مادة (1)
     c. Primary numeric markers    — مادة ١ / المادة 1

2. Sort all hits by position and remove overlapping duplicates.

3. Extract the article body as the text between consecutive markers.
   Text before the first marker is saved as orphan_text (reviewed in
   Stage 2.5).

4. Assign article_id, article_type, sequence_index.

5. Write articles.json + split_report.json to the output directory.

Input:   data/extracted_clean/{LAW_ID}.txt
Output:  data/split_articles/{LAW_ID}/articles.json
         data/split_articles/{LAW_ID}/split_report.json

Note on normalised patterns
---------------------------
Stage 1.3 applies Hamza normalisation (أإآٱ → ا) and Yeh normalisation
(ى → ي).  All regex patterns here are written for the normalised forms:
  - الأولى → الاولي
  - مادة / المادة are unaffected (no Hamza or Yeh).
"""

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from config.law_registry import LawEntry
from config.settings import EXTRACTED_CLEAN_DIR, SPLIT_ARTICLES_DIR

logger = logging.getLogger(__name__)

# ── Arabic numeral translation ───────────────────────────────────────────────

_EASTERN_TO_WESTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _to_int(s: str) -> int:
    """Convert Arabic-Eastern or Western digit string to int."""
    return int(s.strip().translate(_EASTERN_TO_WESTERN))


# ── ordinal lookup (normalised forms) ────────────────────────────────────────
# Keys are the Arabic ordinal words as they appear AFTER Stage 1.3 normalisation.
# Priority ordering matters: longer/more-specific keys must be matched first.

_ORDINAL_TO_INT: dict[str, int] = {
    # compound (must precede simple so regex alternation hits them first)
    "الحادي والعشرين": 21, "الحادي عشر": 11,
    "الثاني عشر": 12,  "الثالث عشر": 13,
    "الرابع عشر": 14,  "الخامس عشر": 15,
    "السادس عشر": 16,  "السابع عشر": 17,
    "الثامن عشر": 18,  "التاسع عشر": 19,
    "العشرون": 20,
    # simple
    "الاولي": 1, "الاول": 1,
    "الثانية": 2, "الثاني": 2,
    "الثالثة": 3, "الثالث": 3,
    "الرابعة": 4, "الرابع": 4,
    "الخامسة": 5, "الخامس": 5,
    "السادسة": 6, "السادس": 6,
    "السابعة": 7, "السابع": 7,
    "الثامنة": 8, "الثامن": 8,
    "التاسعة": 9, "التاسع": 9,
    "العاشرة": 10, "العاشر": 10,
}

# ── compiled patterns (for normalised text) ───────────────────────────────────

# Ordinal string inside issuance marker — longest alternatives first
_ORD_ALT = (
    r"الحادي\s+والعشرين|الحادي\s+عشر[ة]?|الثاني\s+عشر[ة]?"
    r"|الثالث\s+عشر[ة]?|الرابع\s+عشر[ة]?|الخامس\s+عشر[ة]?"
    r"|السادس\s+عشر[ة]?|السابع\s+عشر[ة]?|الثامن\s+عشر[ة]?"
    r"|التاسع\s+عشر[ة]?|العشرون|العاشر[ة]?|التاسع[ة]?"
    r"|الثامن[ة]?|السابع[ة]?|السادس[ة]?|الخامس[ة]?"
    r"|الرابع[ة]?|الثالث[ة]?|الثاني[ة]?|الاولي|الاول"
)

# Optional Markdown prefix (Gemini OCR wraps markers in ### headings)
# Matches both plain "مادة ١" and "### مادة ١"
# Use [ \t]* not \s* to avoid consuming newlines (which would corrupt split boundaries)
_MD = r"^#{0,6}[ \t]*"

# (المادة الاولي) / (المادة الثانية) … — issuance articles
# ^ with MULTILINE anchors to line start → avoids matching inside article bodies
_ISSUANCE_RE = re.compile(
    rf"{_MD}\(المادة\s+(?:{_ORD_ALT})\)",
    re.MULTILINE,
)

# مادة (١) / مادة ( 5 ) — paren-digit articles
# Line-start anchor prevents matching cross-references like "وفقاً لمادة (8)"
_PAREN_DIGIT_RE = re.compile(
    rf"{_MD}مادة\s*\(\s*(?:\d+|[٠-٩]+)\s*\)",
    re.MULTILINE,
)

# مادة ١ / المادة 1 / ### مادة ١  — primary numeric articles
# Line-start anchor prevents matching mid-sentence references.
#
# Two negative lookaheads after the digit group:
#
#   (?![\d٠-٩])         — No more digits.  Prevents regex backtracking from
#                          partially matching a longer number: if "المادة 864"
#                          is followed by a comma the engine would otherwise
#                          retreat to "86", find "4" is not a comma, and
#                          incorrectly report article 86.  This lookahead
#                          makes the digit group effectively atomic.
#
#   (?![ \t\n]*[،,])    — Not immediately followed by an Arabic/Latin comma
#                          (with optional whitespace/newlines in between).
#                          Rejects cross-references like:
#                            "المادة 864\n ، فان لم تتحقق…"
#                          where PDF line-wrap places "المادة" at line start
#                          but the context is a reference inside another article.
_PRIMARY_RE = re.compile(
    rf"{_MD}(?:مادة|المادة)\s+(?:\d+|[٠-٩]+)(?![\d٠-٩])(?![ \t\n]*[،,])",
    re.MULTILINE,
)

# Extract just the digit portion from a matched marker string
_DIGIT_RE = re.compile(r"[\d٠-٩]+")


# ── internal types ────────────────────────────────────────────────────────────

class _Hit(NamedTuple):
    pos: int       # character start position in text
    end: int       # character end position
    number: int    # parsed article number (1-based)
    kind: str      # "ordinal" | "paren_digit" | "primary"
    raw: str       # matched marker text (for debugging)


# ── dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class ArticleRecord:
    article_id: str
    law_id: str
    article_number: int
    article_number_raw: str        # the marker text as it appears in source
    article_type: str              # "issuance" | "main"
    text: str                      # article body (cleaned)
    is_repealed: bool
    sequence_index: int            # 1-based position in document order
    marker_kind: str               # "ordinal" | "paren_digit" | "primary"
    split_source: str              # "regex" | "llm"
    word_count: int
    char_count: int


@dataclass
class SplitReport:
    law_id: str
    articles_found: int
    expected_article_count: int
    issuance_count: int
    main_count: int
    orphan_text_chars: int
    marker_kinds: dict
    split_source: str
    split_at: str


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalise_spaces(s: str) -> str:
    """Collapse runs of whitespace, strip."""
    return re.sub(r"\s+", " ", s).strip()


def _parse_ordinal_number(marker_text: str) -> tuple[int, str]:
    """
    Extract ordinal word from an issuance marker like '(المادة الاولي)'.
    Returns (int_value, ordinal_word).
    """
    # strip outer parens and 'المادة'
    inner = marker_text.strip("()").replace("المادة", "").strip()
    # normalise internal whitespace for dict lookup
    inner_key = re.sub(r"\s+", " ", inner).strip()
    # try exact match first
    if inner_key in _ORDINAL_TO_INT:
        return _ORDINAL_TO_INT[inner_key], inner_key
    # try prefix matching for variants (e.g. الاول vs الاولي)
    for key, val in _ORDINAL_TO_INT.items():
        if inner_key.startswith(key) or key.startswith(inner_key):
            return val, inner_key
    logger.warning("Could not parse ordinal '%s' — defaulting to 0", inner_key)
    return 0, inner_key


def _parse_digit_number(marker_text: str) -> int:
    """Extract the integer from a primary or paren-digit marker."""
    m = _DIGIT_RE.search(marker_text)
    if not m:
        return 0
    return _to_int(m.group())


def _collect_hits(text: str) -> list[_Hit]:
    """Find all article-marker positions, sorted and deduplicated."""
    raw_hits: list[_Hit] = []

    for m in _ISSUANCE_RE.finditer(text):
        num, _ = _parse_ordinal_number(m.group())
        if num > 0:
            raw_hits.append(_Hit(m.start(), m.end(), num, "ordinal", m.group()))

    for m in _PAREN_DIGIT_RE.finditer(text):
        num = _parse_digit_number(m.group())
        raw_hits.append(_Hit(m.start(), m.end(), num, "paren_digit", m.group()))

    for m in _PRIMARY_RE.finditer(text):
        num = _parse_digit_number(m.group())
        raw_hits.append(_Hit(m.start(), m.end(), num, "primary", m.group()))

    # Sort: by position; for ties prefer longer match (more specific)
    raw_hits.sort(key=lambda h: (h.pos, -(h.end - h.pos)))

    # Deduplicate overlapping matches (keep whichever started first)
    deduped: list[_Hit] = []
    prev_end = -1
    for h in raw_hits:
        if h.pos >= prev_end:
            deduped.append(h)
            prev_end = h.end

    return deduped


# ── public API ────────────────────────────────────────────────────────────────

def run(law_entry: LawEntry) -> tuple[list[ArticleRecord], SplitReport]:
    """
    Split *law_entry*'s cleaned text into article records.

    Returns (articles, report).  Writes articles.json + split_report.json
    to data/split_articles/{law_id}/.
    """
    clean_path = EXTRACTED_CLEAN_DIR / f"{law_entry.law_id}.txt"
    if not clean_path.exists():
        raise FileNotFoundError(
            f"Cleaned text not found at {clean_path}. Run Stage 1→1.3 first."
        )

    text = clean_path.read_text(encoding="utf-8")
    hits = _collect_hits(text)

    if not hits:
        logger.error("[%s] No article markers found — cannot split.", law_entry.law_id)
        raise ValueError(
            f"[{law_entry.law_id}] No article markers found in cleaned text. "
            "Check extraction quality or update regex patterns."
        )

    # Orphan text: everything before the first marker
    orphan_text = text[: hits[0].pos].strip()
    orphan_chars = len(orphan_text)

    # Build article records
    articles: list[ArticleRecord] = []
    repealed_set = set(law_entry.repealed_articles)

    for seq_idx, hit in enumerate(hits, start=1):
        body_start = hit.end
        body_end = hits[seq_idx].pos if seq_idx < len(hits) else len(text)
        body = text[body_start:body_end].strip()

        # Strip any trailing colon that appears as part of masaar.net formatting
        body = re.sub(r"^:\s*", "", body)

        article_type = "issuance" if hit.kind == "ordinal" else "main"
        article_id = f"{law_entry.law_id}_{seq_idx:03d}"
        is_repealed = hit.number in repealed_set and article_type == "main"

        rec = ArticleRecord(
            article_id=article_id,
            law_id=law_entry.law_id,
            article_number=hit.number,
            article_number_raw=_normalise_spaces(hit.raw),
            article_type=article_type,
            text=body,
            is_repealed=is_repealed,
            sequence_index=seq_idx,
            marker_kind=hit.kind,
            split_source="regex",
            word_count=len(body.split()),
            char_count=len(body),
        )
        articles.append(rec)

    # ── Post-process: re-classify pre-article-1 entries as issuance ───────────
    # Some PDFs (e.g. القانون المدني) encode issuance articles using the same
    # numeric format as main articles ("مادة 2 – على وزير العدل…") rather than
    # the ordinal form "(المادة الثانية)".  The splitter assigns them type="main"
    # because they match _PRIMARY_RE.  To fix this without a law-specific hack:
    #   • find the sequence index of the first occurrence of article 1
    #   • any articles that appear before it (in document order) and were
    #     classified as "main" are actually issuance articles
    first_art1_seq = next(
        (a.sequence_index for a in articles if a.article_number == 1 and a.article_type == "main"),
        None,
    )
    if first_art1_seq is not None and first_art1_seq > 1:
        for a in articles:
            if a.sequence_index < first_art1_seq and a.article_type == "main":
                a.article_type = "issuance"
                logger.info(
                    "[%s] Re-classified article %d (seq=%d) as issuance "
                    "(appears before article 1 in document order)",
                    law_entry.law_id, a.article_number, a.sequence_index,
                )

    # Tally marker kinds
    kind_counts: dict[str, int] = {}
    for h in hits:
        kind_counts[h.kind] = kind_counts.get(h.kind, 0) + 1

    issuance_count = sum(1 for a in articles if a.article_type == "issuance")
    main_count = len(articles) - issuance_count

    report = SplitReport(
        law_id=law_entry.law_id,
        articles_found=len(articles),
        expected_article_count=law_entry.expected_article_count,
        issuance_count=issuance_count,
        main_count=main_count,
        orphan_text_chars=orphan_chars,
        marker_kinds=kind_counts,
        split_source="regex",
        split_at=datetime.now(timezone.utc).isoformat(),
    )

    # Write outputs
    out_dir = SPLIT_ARTICLES_DIR / law_entry.law_id
    out_dir.mkdir(parents=True, exist_ok=True)

    articles_path = out_dir / "articles.json"
    articles_path.write_text(
        json.dumps([asdict(a) for a in articles], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report_path = out_dir / "split_report.json"
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "[%s] Split complete — %d articles (%d issuance + %d main) | "
        "expected=%d | orphan=%d chars",
        law_entry.law_id,
        len(articles), issuance_count, main_count,
        law_entry.expected_article_count, orphan_chars,
    )
    return articles, report
