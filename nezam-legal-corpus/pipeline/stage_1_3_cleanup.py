"""
Stage 1.3: Arabic Text Cleanup

Normalises the raw extracted text and produces a character-level audit log
so reviewers can see exactly what changed.

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

# ── compiled patterns ────────────────────────────────────────────────────────

_TATWEEL       = re.compile(r"\u0640+")
_DIACRITICS    = re.compile(r"[\u064B-\u065F\u0670]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_SPACE   = re.compile(r"[^\S\n]+")          # any whitespace except newline
_MULTI_NEWLINE = re.compile(r"\n{3,}")

_HAMZA_MAP = str.maketrans("أإآٱ", "اااا")
_YEH_MAP   = str.maketrans("ىئ", "يي")

# ── dataclass ────────────────────────────────────────────────────────────────

@dataclass
class CleanupAudit:
    law_id: str
    extraction_source: str
    chars_before: int
    chars_after: int
    chars_removed: int

    nfc_changed: int          # code-points altered by NFC normalisation
    tatweel_removed: int      # U+0640 characters removed
    diacritics_removed: int   # harakat / shadda / etc.
    hamza_normalised: int     # أإآٱ → ا
    yeh_normalised: int       # ىئ → ي
    control_removed: int      # non-printable control characters
    spaces_collapsed: int     # runs of horizontal whitespace collapsed
    newlines_collapsed: int   # runs of 3+ newlines collapsed to 2

    cleaned_at: str


# ── internal helpers ─────────────────────────────────────────────────────────

def _count_char_matches(pattern: re.Pattern, text: str) -> int:
    """Total characters consumed by all matches of *pattern* in *text*."""
    return sum(len(m.group()) for m in pattern.finditer(text))


def _count_translate_changes(text: str, table: dict) -> int:
    """Count characters that will change when *table* is applied via str.translate."""
    return sum(1 for ch in text if ord(ch) in table)


# ── public API ───────────────────────────────────────────────────────────────

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

    # ── Step 1: NFC normalisation ────────────────────────────────────────────
    nfc = unicodedata.normalize("NFC", raw)
    nfc_changed = sum(
        1 for a, b in zip(raw, nfc) if a != b
    ) + abs(len(nfc) - len(raw))

    working = nfc

    # ── Step 2: remove tatweel ───────────────────────────────────────────────
    tatweel_removed = _count_char_matches(_TATWEEL, working)
    working = _TATWEEL.sub("", working)

    # ── Step 3: remove diacritics ────────────────────────────────────────────
    diacritics_removed = _count_char_matches(_DIACRITICS, working)
    working = _DIACRITICS.sub("", working)

    # ── Step 4: normalise Hamza variants (أإآٱ → ا) ──────────────────────────
    hamza_normalised = _count_translate_changes(working, _HAMZA_MAP)
    working = working.translate(_HAMZA_MAP)

    # ── Step 5: normalise Yeh variants (ىئ → ي) ──────────────────────────────
    yeh_normalised = _count_translate_changes(working, _YEH_MAP)
    working = working.translate(_YEH_MAP)

    # ── Step 6: remove control characters ───────────────────────────────────
    control_removed = _count_char_matches(_CONTROL_CHARS, working)
    working = _CONTROL_CHARS.sub("", working)

    # ── Step 7: collapse horizontal whitespace ───────────────────────────────
    spaces_collapsed = sum(
        max(0, len(m.group()) - 1)
        for m in _MULTI_SPACE.finditer(working)
        if len(m.group()) > 1
    )
    working = _MULTI_SPACE.sub(" ", working)

    # ── Step 8: collapse excess newlines ────────────────────────────────────
    newlines_collapsed = sum(
        len(m.group()) - 2
        for m in _MULTI_NEWLINE.finditer(working)
    )
    working = _MULTI_NEWLINE.sub("\n\n", working)

    clean = working.strip()
    chars_after = len(clean)

    # ── write clean text ─────────────────────────────────────────────────────
    EXTRACTED_CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    clean_path = EXTRACTED_CLEAN_DIR / f"{law_entry.law_id}.txt"
    clean_path.write_text(clean, encoding="utf-8")

    # ── build + write audit ──────────────────────────────────────────────────
    audit = CleanupAudit(
        law_id=law_entry.law_id,
        extraction_source=extraction_source,
        chars_before=chars_before,
        chars_after=chars_after,
        chars_removed=chars_before - chars_after,
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
        "tatweel=%d diacritics=%d hamza=%d yeh=%d control=%d)",
        law_entry.law_id,
        chars_before, chars_after, chars_before - chars_after,
        tatweel_removed, diacritics_removed,
        hamza_normalised, yeh_normalised, control_removed,
    )
    return audit
