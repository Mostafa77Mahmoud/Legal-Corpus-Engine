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
# Covers Arabic-Eastern (٠-٩) and Persian/Extended-Arabic (۰-۹) digit forms.
# Both appear in Egyptian legal PDFs when OCR uses different Unicode blocks.

_EASTERN_TO_WESTERN = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩"   # Arabic-Eastern (U+0660..U+0669)
    "۰۱۲۳۴۵۶۷۸۹",  # Persian/Extended-Arabic (U+06F0..U+06F9)
    "01234567890123456789",
)


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
# Also tolerates 0-2 stray punctuation characters directly before the marker word
# (e.g. OCR emitting ":مادة (372)" at a genuine line start) — bounded repetition,
# fixed punctuation class only, never matches free text.
_MD = r"^#{0,6}[ \t]*[:؛.,\-()]{0,2}[ \t]*"

# (المادة الاولي) / (المادة الثانية) / ( المادة الاولي ) … — issuance articles
# Optional whitespace after "(" and before ")" — Gemini OCR sometimes inserts
# a space inside the parentheses ("( المادة الاولي )") depending on source PDF.
# ^ with MULTILINE anchors to line start → avoids matching inside article bodies
_ISSUANCE_RE = re.compile(
    rf"{_MD}\(\s*المادة\s+(?:{_ORD_ALT})\s*\)",
    re.MULTILINE,
)

# Digit character class covering Latin, Arabic-Eastern, and Persian digits
_DIGITS = r"[\d٠-٩۰-۹]"

# مادة word — accepts تاء مربوطة (ة) or open heh (ه) — OCR sometimes outputs ماده
_MADA_WORD = r"(?:مادة|ماده|المادة|الماده)"

# Cross-reference lookahead — rejects markers immediately followed by a
# reference phrase such as "من هذا القانون" / "من ذلك القانون" / "من القانون
# المدني" / "من المشروع".  PDF line-wrap can place "مادة (N)" / "المادة N" at
# the start of a line even when it is a mid-sentence citation of an article
# (in this law, another law, or the bill/draft being explained by an
# accompanying "مذكرة إيضاحية") rather than a real article header.  Real
# article headers are followed by a colon or body text, never by these
# reference phrases.
_NOT_LAW_REFERENCE = r"(?![ \t\n]*من\s+(?:هذا\s+|ذلك\s+)?(?:القانون|المشروع))"

# Optional "مكرر" (bis) suffix — Egyptian legislative convention for inserting
# a new article after an existing one without renumbering the whole law
# (e.g. "المادة 148 مكرر", "148 مكررا", "148 مكرر أ", "148 مكرر ب"). Consumed
# as part of the marker itself (not left as leading article-body text), and
# an optional trailing sub-letter distinguishes multiple bis articles inserted
# after the same base number. Tolerates an extra stray paren on either side,
# since OCR sometimes emits "(148) مكررا)" instead of "(148 مكررا)".
_BIS_WORD = r"مكرر[ةا]?"
_BIS_SUFFIX = rf"(?:\s*\)?\s*{_BIS_WORD}(?:\s+[أ-ي](?=[\s:\)]))?\s*\)?)?"

# مادة (١) / مادة ( 5 ) — paren-digit articles
# Line-start anchor prevents matching cross-references like "وفقاً لمادة (8)"
_PAREN_DIGIT_RE = re.compile(
    rf"{_MD}{_MADA_WORD}\s*\(\s*{_DIGITS}+\s*\){_BIS_SUFFIX}{_NOT_LAW_REFERENCE}",
    re.MULTILINE,
)

# مادة ١ / المادة 1 / ### مادة ١ / ماده ۹  — primary numeric articles
# Line-start anchor prevents matching mid-sentence references.
#
# Three negative lookaheads after the digit group:
#
#   (?![\d٠-٩۰-۹])     — No more digits (any script).  Prevents backtracking
#                          from partially matching a longer number.
#
#   (?![ \t\n]*[،,])    — Not immediately followed by an Arabic/Latin comma.
#                          Rejects cross-references like:
#                            "المادة 864\n ، فان لم تتحقق…"
#
#   _NOT_LAW_REFERENCE  — Not immediately followed by "من (هذا/ذلك) القانون".
#                          Rejects cross-references like:
#                            "المادة 143 من هذا القانون"
_PRIMARY_RE = re.compile(
    rf"{_MD}{_MADA_WORD}\s+{_DIGITS}+(?![\d٠-٩۰-۹])(?![ \t\n]*[،,]){_BIS_SUFFIX}{_NOT_LAW_REFERENCE}",
    re.MULTILINE,
)

# Extract just the digit portion from a matched marker string
_DIGIT_RE = re.compile(r"[\d٠-٩۰-۹]+")


# ── internal types ────────────────────────────────────────────────────────────

class _Hit(NamedTuple):
    pos: int       # character start position in text
    end: int       # character end position
    number: int    # parsed article number (1-based)
    kind: str      # "ordinal" | "paren_digit" | "primary" | "bis"
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
    marker_kind: str               # "ordinal" | "paren_digit" | "primary" | "bis"
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


_MANUAL_EXCLUSION_LOOKBACK = 40  # chars of preceding context to check


def _collect_hits(
    text: str, manual_marker_exclusions: list[str] | None = None
) -> list[_Hit]:
    """Find all article-marker positions, sorted and deduplicated.

    *manual_marker_exclusions*: optional list of human-reviewed phrases
    (see `config.law_registry.LawEntry.manual_marker_exclusions`). A hit is
    dropped if any of these phrases appears in the text immediately
    preceding the marker (within `_MANUAL_EXCLUSION_LOOKBACK` chars,
    whitespace-normalised). This is a per-document, human-audited exception
    list — never a general pattern.
    """
    raw_hits: list[_Hit] = []

    for m in _ISSUANCE_RE.finditer(text):
        num, _ = _parse_ordinal_number(m.group())
        if num > 0:
            raw_hits.append(_Hit(m.start(), m.end(), num, "ordinal", m.group()))

    for m in _PAREN_DIGIT_RE.finditer(text):
        num = _parse_digit_number(m.group())
        kind = "bis" if "مكرر" in m.group() else "paren_digit"
        raw_hits.append(_Hit(m.start(), m.end(), num, kind, m.group()))

    for m in _PRIMARY_RE.finditer(text):
        num = _parse_digit_number(m.group())
        kind = "bis" if "مكرر" in m.group() else "primary"
        raw_hits.append(_Hit(m.start(), m.end(), num, kind, m.group()))

    if manual_marker_exclusions:
        filtered: list[_Hit] = []
        for h in raw_hits:
            context = _normalise_spaces(
                text[max(0, h.pos - _MANUAL_EXCLUSION_LOOKBACK): h.pos]
            )
            if any(phrase in context for phrase in manual_marker_exclusions):
                logger.info(
                    "Manually excluded marker %r at pos=%d (preceding context: %r)",
                    h.raw, h.pos, context,
                )
                continue
            filtered.append(h)
        raw_hits = filtered

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
    hits = _collect_hits(text, law_entry.manual_marker_exclusions)

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
    # because they match _PRIMARY_RE.
    #
    # Other PDFs (e.g. EG_RENT_1969) prepend a full "مذكرة إيضاحية" (explanatory
    # memorandum) that narrates the bill article-by-article using the exact same
    # "مادة 1" … "مادة N" numbering as the codified law that follows — i.e. the
    # numbering sequence 1..N genuinely repeats twice in the raw text before the
    # real codified text is reached.  To handle both cases without a law-specific
    # hack, anchor on the LAST occurrence of article 1 (not the first): anything
    # before that point is preamble/issuance content, and the codified law text
    # that actually defines articles 1..N starts there.
    last_art1_seq = next(
        (a.sequence_index for a in reversed(articles) if a.article_number == 1 and a.article_type == "main"),
        None,
    )
    if last_art1_seq is not None and last_art1_seq > 1:
        for a in articles:
            if a.sequence_index < last_art1_seq and a.article_type == "main":
                a.article_type = "issuance"
                logger.info(
                    "[%s] Re-classified article %d (seq=%d) as issuance "
                    "(appears before the final/codified article 1 in document order)",
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
