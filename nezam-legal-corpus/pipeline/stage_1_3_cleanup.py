"""
Stage 1.3: Arabic Text Cleanup

Normalises the raw extracted text and produces a character-level audit log
so reviewers can see exactly what changed.

Normalisation pipeline (in order):
  0. NFKC pre-pass for Arabic Presentation Forms (ligature PDFs)
     — Some PDF generators (e.g. Ministry of Finance, pre-2010 tools) encode
       Arabic in Unicode Presentation Forms (U+FE70–FEFF) rather than standard
       Arabic (U+0600–U+06FF).  After PyMuPDF extraction the text is readable
       visually but all regex patterns (Stage 2 splitter, Stage 1.5 marker
       counts) operate on standard Arabic codepoints and will fail to match.
       NFKC compatibility decomposition converts presentation forms to their
       canonical equivalents.  NFKC is applied ONLY when presentation forms
       are detected (> 1% of characters), leaving clean PDFs / TXT unchanged.
  1. NFC normalisation (canonical composition)
  2. Tatweel removal (U+0640)
  3. Diacritics removal (harakat, shadda, etc.)
  4. Hamza normalisation (أإآٱ → ا)
  5. Yeh normalisation (ىئ → ي)
  6. Control-character removal
  7. Horizontal whitespace collapse
  8. Excess newline collapse

Input:   data/extracted_raw/{LAW_ID}.txt
Output:  data/extracted_clean/{LAW_ID}.txt
         data/cleanup_audit_logs/{LAW_ID}_cleanup_audit.json
"""

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from config.law_registry import LawEntry
from config.settings import CLEANUP_AUDIT_DIR, EXTRACTED_CLEAN_DIR, EXTRACTED_RAW_DIR

logger = logging.getLogger(__name__)

# ── compiled patterns ─────────────────────────────────────────────────────────

_PRES_FORMS_A = re.compile(r"[\uFB50-\uFDFF]")   # Arabic Presentation Forms-A
_PRES_FORMS_B = re.compile(r"[\uFE70-\uFEFF]")   # Arabic Presentation Forms-B
_TATWEEL       = re.compile(r"\u0640+")
_DIACRITICS    = re.compile(r"[\u064B-\u065F\u0670]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_SPACE   = re.compile(r"[^\S\n]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")

_HAMZA_MAP = str.maketrans("أإآٱ", "اااا")
_YEH_MAP   = str.maketrans("ىئ", "يي")

# Threshold: apply NFKC pre-pass only when presentation forms exceed this
# fraction of total characters (avoids false-positive NFKC on clean texts)
_PRES_FORMS_THRESHOLD = 0.01  # 1%


# ── dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class CleanupAudit:
    law_id: str
    extraction_source: str
    chars_before: int
    chars_after: int
    chars_removed: int

    presentation_forms_normalized: int  # chars converted by NFKC pre-pass
    nfc_changed: int          # code-points altered by NFC normalisation
    tatweel_removed: int      # U+0640 characters removed
    diacritics_removed: int   # harakat / shadda / etc.
    hamza_normalised: int     # أإآٱ → ا
    yeh_normalised: int       # ىئ → ي
    control_removed: int      # non-printable control characters
    spaces_collapsed: int     # runs of horizontal whitespace collapsed
    newlines_collapsed: int   # runs of 3+ newlines collapsed to 2

    cleaned_at: str


# ── internal helpers ──────────────────────────────────────────────────────────

def _count_char_matches(pattern: re.Pattern, text: str) -> int:
    return sum(len(m.group()) for m in pattern.finditer(text))


def _count_translate_changes(text: str, table: dict) -> int:
    return sum(1 for ch in text if ord(ch) in table)


def _count_presentation_forms(text: str) -> int:
    return (
        len(_PRES_FORMS_A.findall(text))
        + len(_PRES_FORMS_B.findall(text))
    )


# ── public API ────────────────────────────────────────────────────────────────

def run(law_entry: LawEntry, extraction_source: str = "unknown") -> CleanupAudit:
    """
    Clean the raw extracted text for *law_entry* and write the result plus
    an audit log.  Returns a CleanupAudit dataclass.
    """
    raw_path = EXTRACTED_RAW_DIR / f"{law_entry.law_id}.txt"
    if not raw_path.exists():
        raise FileNotFoundError(
            f"Raw text not found at {raw_path}. Run Stage 1 first."
        )

    raw = raw_path.read_text(encoding="utf-8")
    chars_before = len(raw)
    working = raw

    # ── Step 0: NFKC pre-pass (Arabic Presentation Forms → standard Arabic) ──
    pres_count = _count_presentation_forms(working)
    pres_fraction = pres_count / chars_before if chars_before else 0.0

    if pres_fraction >= _PRES_FORMS_THRESHOLD:
        logger.info(
            "[%s] Arabic Presentation Forms detected (%.1f%% of chars) — "
            "applying NFKC normalisation",
            law_entry.law_id, pres_fraction * 100,
        )
        before_nfkc = working
        working = unicodedata.normalize("NFKC", working)
        presentation_forms_normalized = sum(
            1 for a, b in zip(before_nfkc, working) if a != b
        ) + abs(len(working) - len(before_nfkc))
    else:
        presentation_forms_normalized = 0

    # ── Step 1: NFC normalisation ─────────────────────────────────────────────
    nfc = unicodedata.normalize("NFC", working)
    nfc_changed = sum(
        1 for a, b in zip(working, nfc) if a != b
    ) + abs(len(nfc) - len(working))
    working = nfc

    # ── Step 2: remove tatweel ────────────────────────────────────────────────
    tatweel_removed = _count_char_matches(_TATWEEL, working)
    working = _TATWEEL.sub("", working)

    # ── Step 3: remove diacritics ─────────────────────────────────────────────
    diacritics_removed = _count_char_matches(_DIACRITICS, working)
    working = _DIACRITICS.sub("", working)

    # ── Step 4: normalise Hamza variants (أإآٱ → ا) ───────────────────────────
    hamza_normalised = _count_translate_changes(working, _HAMZA_MAP)
    working = working.translate(_HAMZA_MAP)

    # ── Step 5: normalise Yeh variants (ىئ → ي) ───────────────────────────────
    yeh_normalised = _count_translate_changes(working, _YEH_MAP)
    working = working.translate(_YEH_MAP)

    # ── Step 6: remove control characters ────────────────────────────────────
    control_removed = _count_char_matches(_CONTROL_CHARS, working)
    working = _CONTROL_CHARS.sub("", working)

    # ── Step 7: collapse horizontal whitespace ────────────────────────────────
    spaces_collapsed = sum(
        max(0, len(m.group()) - 1)
        for m in _MULTI_SPACE.finditer(working)
        if len(m.group()) > 1
    )
    working = _MULTI_SPACE.sub(" ", working)

    # ── Step 8: collapse excess newlines ──────────────────────────────────────
    newlines_collapsed = sum(
        len(m.group()) - 2
        for m in _MULTI_NEWLINE.finditer(working)
    )
    working = _MULTI_NEWLINE.sub("\n\n", working)

    clean = working.strip()
    chars_after = len(clean)

    # ── write clean text ──────────────────────────────────────────────────────
    EXTRACTED_CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    clean_path = EXTRACTED_CLEAN_DIR / f"{law_entry.law_id}.txt"
    clean_path.write_text(clean, encoding="utf-8")

    # ── build + write audit ───────────────────────────────────────────────────
    audit = CleanupAudit(
        law_id=law_entry.law_id,
        extraction_source=extraction_source,
        chars_before=chars_before,
        chars_after=chars_after,
        chars_removed=chars_before - chars_after,
        presentation_forms_normalized=presentation_forms_normalized,
        nfc_changed=nfc_changed,
        tatweel_removed=tatweel_removed,
        diacritics_removed=diacritics_removed,
        hamza_normalised=hamza_normalised,
        yeh_normalised=yeh_normalised,
        control_removed=control_removed,
        spaces_collapsed=spaces_collapsed,
        newlines_collapsed=newlines_collapsed,
        cleaned_at=datetime.now(timezone.utc).isoformat(),
    )

    CLEANUP_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = CLEANUP_AUDIT_DIR / f"{law_entry.law_id}_cleanup_audit.json"
    audit_path.write_text(
        json.dumps(asdict(audit), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "[%s] Cleanup done — %d → %d chars (removed %d | "
        "pres_forms=%d nfc=%d tatweel=%d diacritics=%d hamza=%d yeh=%d ctrl=%d)",
        law_entry.law_id,
        chars_before, chars_after, chars_before - chars_after,
        presentation_forms_normalized, nfc_changed,
        tatweel_removed, diacritics_removed,
        hamza_normalised, yeh_normalised, control_removed,
    )
    return audit
