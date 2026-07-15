import re
import unicodedata


_ARABIC_RANGE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

_HAMZA_VARIANTS = str.maketrans(
    "أإآاٱ",
    "اااا" + "ا",
)
_YEH_VARIANTS = str.maketrans("ىئ", "يي")
_HEH_VARIANTS = str.maketrans("ةه", "هه")
_TATWEEL = re.compile(r"\u0640+")
_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")
_MULTI_SPACE = re.compile(r"[^\S\n]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_REPLACEMENT_CHAR = re.compile(r"\ufffd")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Arabic ordinal words (masculine and feminine).
# "الأول" also matches the hamza-less "الاول" spelling, since Stage 1.3
# cleanup's hamza normalisation (أإآٱ → ا) turns "الأول" into "الاول" in the
# text this counter runs on — the original hamza spelling is still accepted
# for callers that run it on pre-cleanup text.
_ORDINALS = (
    r"ال[أا]ول[ىة]?|الثاني[ة]?|الثالث[ة]?|الرابع[ة]?|الخامس[ة]?"
    r"|السادس[ة]?|السابع[ة]?|الثامن[ة]?|التاسع[ة]?|العاشر[ة]?"
    r"|الحادي\s+عشر[ة]?|الثاني\s+عشر[ة]?|الثالث\s+عشر[ة]?|الرابع\s+عشر[ة]?"
    r"|الخامس\s+عشر[ة]?|السادس\s+عشر[ة]?|السابع\s+عشر[ة]?|الثامن\s+عشر[ة]?"
    r"|التاسع\s+عشر[ة]?|العشرون|الحادي\s+والعشرون"
)

# Article marker pattern for abbreviated/garbled markers not handled by the
# Stage 2 splitter (see count_article_markers below for the primary formats,
# which delegate to pipeline.stage_2_split for consistency).
_ART_ABBREV = re.compile(r"(?<!\w)ما\s{0,3}(?=\d)(?:\d+|[٠-٩]+)", re.MULTILINE)

# Structural heading patterns (all supported formats)
_HEAD_PLAIN = re.compile(
    rf"(?:الباب|الفصل|القسم|الكتاب|الفرع)\s+(?:{_ORDINALS}|\d+|[٠-٩]+)",
    re.MULTILINE,
)
_HEAD_PAREN = re.compile(
    rf"\((?:الباب|الفصل|القسم|الكتاب|الفرع)\s+(?:{_ORDINALS}|\d+|[٠-٩]+)\)",
    re.MULTILINE,
)

# Website boilerplate markers used by Egyptian legal aggregators (e.g. Masaar)
_TXT_CONTENT_START_MARKERS = ["نص التشريع", "نص القانون", "نص اللائحة"]
_TXT_FOOTER_MARKERS = [
    "Creative Commons",
    "← قانون",
    "سياسة الخصوصية",
    "\nIcons and photos",
]


def normalize(text: str, remove_diacritics: bool = True) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _TATWEEL.sub("", text)
    if remove_diacritics:
        text = _DIACRITICS.sub("", text)
    text = text.translate(_HAMZA_VARIANTS)
    text = text.translate(_YEH_VARIANTS)
    text = _CONTROL_CHARS.sub("", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def arabic_char_density(text: str) -> float:
    if not text:
        return 0.0
    arabic_count = len(_ARABIC_RANGE.findall(text))
    return arabic_count / len(text)


def replacement_char_density(text: str) -> float:
    if not text:
        return 0.0
    replacement_count = len(_REPLACEMENT_CHAR.findall(text))
    return replacement_count / len(text)


def count_article_markers(
    text: str, manual_marker_exclusions: list[str] | None = None
) -> int:
    """
    Count distinct article markers supporting all known Egyptian law PDF/TXT formats:

    - Standard:       مادة 5 / المادة 5
    - Abbreviated:    ما5 / ما 6          (garbled/encoding-broken PDFs)
    - Paren-digit:    مادة (١) / مادة ( ٢ )  (Masaar TXT and some PDFs)
    - Ordinal-paren:  (المادة الأولى) ... (المادة السابعة)  (issuance articles)

    Delegates the standard/paren-digit/ordinal detection to
    ``pipeline.stage_2_split``'s hit-collector so that Stage 1.5 (confidence
    scoring) and Stage 2 (splitting) always agree on what counts as a real
    article marker. Import is deferred to avoid a module-load cycle, since
    ``stage_2_split`` is a pipeline module and this is a low-level util.

    *manual_marker_exclusions*: optional per-law human-reviewed exclusion
    list (see ``config.law_registry.LawEntry.manual_marker_exclusions``),
    forwarded so Stage 1.5's confidence count matches Stage 2's final split
    count for laws with a documented exclusion.

    The splitter's patterns are anchored to line-start and exclude
    mid-sentence cross-references (e.g. "المادة 604 من القانون المدنى" or
    "المادة (143) من هذا القانون"), and dedupe by *article number* rather
    than by matched string — so "مادة 5" and "المادة 5" referring to the
    same article are counted once, not twice. The abbreviated "ما5" format
    (garbled/encoding-broken PDFs) is not handled by the splitter, so it is
    still counted separately here via number extraction.
    """
    from pipeline.stage_2_split import _collect_hits

    numbers: set[int] = {
        hit.number for hit in _collect_hits(text, manual_marker_exclusions)
    }
    numbers.update(_to_int_digits(m) for m in _ART_ABBREV.findall(text))
    return len(numbers)


_DIGIT_TRANSLATE = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _to_int_digits(raw: str) -> int:
    """Extract the integer value from a raw abbreviated marker like 'ما5'."""
    digits = re.sub(r"\D", "", raw.translate(_DIGIT_TRANSLATE))
    return int(digits) if digits else -1


def count_structural_headings(text: str) -> int:
    """
    Count chapter/section heading OCCURRENCES (positions in the text, not
    distinct heading strings) in plain or parenthesised form:
      - Plain:  الفصل الأول / الباب الثاني
      - Paren:  (الفصل الأول) / (الباب الحادي عشر)

    Uses raw occurrence counts rather than a set: nested subdivisions (e.g.
    الفصل) restart their own ordinal numbering under each parent heading
    (e.g. الباب), so the same heading text (e.g. "الفصل الثاني") legitimately
    recurs multiple times as distinct headings across different parents.
    Deduplicating by text would collapse those into a single count.
    """
    return len(_HEAD_PLAIN.findall(text)) + len(_HEAD_PAREN.findall(text))


def strip_txt_boilerplate(text: str) -> str:
    """
    Remove website navigation headers and Creative-Commons footers injected by
    Egyptian legal aggregator sites (e.g. masaar.net) when the page is saved as TXT.

    Crops to the first recognised content-start marker through the first
    recognised footer marker. If no markers are found, returns text unchanged.
    """
    start_pos = 0
    for marker in _TXT_CONTENT_START_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            start_pos = idx + len(marker)
            break

    end_pos = len(text)
    for marker in _TXT_FOOTER_MARKERS:
        idx = text.find(marker, start_pos)
        if idx != -1:
            end_pos = min(end_pos, idx)

    return text[start_pos:end_pos].strip()
