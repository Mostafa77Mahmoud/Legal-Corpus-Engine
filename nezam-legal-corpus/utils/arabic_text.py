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


def count_article_markers(text: str) -> int:
    pattern = re.compile(r"(?:مادة|المادة)\s+(?:\d+|[٠-٩]+)", re.MULTILINE)
    return len(pattern.findall(text))


def count_structural_headings(text: str) -> int:
    pattern = re.compile(
        r"(?:الباب|الفصل|القسم|الكتاب|الفرع)\s+(?:الأول|الثاني|الثالث|الرابع|الخامس"
        r"|السادس|السابع|الثامن|التاسع|العاشر|\d+|[٠-٩]+)",
        re.MULTILINE,
    )
    return len(pattern.findall(text))
