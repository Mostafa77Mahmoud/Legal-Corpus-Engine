"""Unit tests for utils/arabic_text.py — run with: python -m pytest tests/"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from utils.arabic_text import (
    arabic_char_density,
    count_article_markers,
    count_structural_headings,
    normalize,
    replacement_char_density,
)


def test_arabic_char_density_pure_arabic():
    text = "العقد شريعة المتعاقدين"
    assert arabic_char_density(text) > 0.7


def test_arabic_char_density_empty():
    assert arabic_char_density("") == 0.0


def test_replacement_char_density_clean():
    assert replacement_char_density("نص سليم بالكامل") == 0.0


def test_replacement_char_density_corrupted():
    text = "نص \ufffd\ufffd سليم"
    density = replacement_char_density(text)
    assert density > 0.0


def test_count_article_markers():
    text = "مادة 1\nنص المادة الأولى\n\nمادة 2\nنص المادة الثانية\n\nالمادة 3\nنص"
    assert count_article_markers(text) == 3


def test_count_article_markers_none():
    assert count_article_markers("لا توجد مواد هنا") == 0


def test_count_structural_headings():
    text = "الباب الأول\nمحتوى\n\nالفصل الثاني\nمحتوى"
    assert count_structural_headings(text) == 2


def test_normalize_removes_tatweel():
    assert "ـ" not in normalize("جمـيل")


def test_normalize_removes_diacritics():
    assert "َ" not in normalize("كَتَبَ")


def test_normalize_collapses_newlines():
    text = "سطر\n\n\n\nسطر آخر"
    assert "\n\n\n" not in normalize(text)
