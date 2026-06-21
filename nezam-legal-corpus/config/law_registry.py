from dataclasses import dataclass, field


@dataclass
class LawEntry:
    law_id: str
    law_name_ar: str
    law_number: str
    year: int
    pdf_filename: str
    expected_article_count: int
    repealed_articles: list[int] = field(default_factory=list)
    expected_chapter_headings: int = 0
    notes: str = ""


LAW_REGISTRY: dict[str, LawEntry] = {
    "EG_PDPL": LawEntry(
        law_id="EG_PDPL",
        law_name_ar="قانون حماية البيانات الشخصية",
        law_number="151 لسنة 2020",
        year=2020,
        pdf_filename="EG_PDPL.pdf",
        expected_article_count=43,
        repealed_articles=[],
        expected_chapter_headings=6,
        notes="قانون حديث، PDF رقمي، من المتوقع استخراجه بنجاح عبر PyMuPDF",
    ),
    "EG_EVIDENCE": LawEntry(
        law_id="EG_EVIDENCE",
        law_name_ar="قانون الإثبات في المواد المدنية والتجارية",
        law_number="25 لسنة 1968",
        year=1968,
        pdf_filename="EG_EVIDENCE.pdf",
        expected_article_count=99,
        repealed_articles=[],
        expected_chapter_headings=8,
    ),
    "EG_ESIGN": LawEntry(
        law_id="EG_ESIGN",
        law_name_ar="قانون التوقيع الإلكتروني وإنشاء هيئة تنمية صناعة تكنولوجيا المعلومات",
        law_number="15 لسنة 2004",
        year=2004,
        pdf_filename="EG_ESIGN.pdf",
        expected_article_count=32,
        repealed_articles=[],
        expected_chapter_headings=0,
        notes="PDF has Arabic ligature encoding defect — garbled text, Gemini OCR required",
    ),
    "EG_LABOR": LawEntry(
        law_id="EG_LABOR",
        law_name_ar="قانون العمل",
        law_number="12 لسنة 2003",
        year=2003,
        pdf_filename="EG_LABOR.pdf",
        expected_article_count=254,
        repealed_articles=[],
        expected_chapter_headings=20,
    ),
    "EG_RENT": LawEntry(
        law_id="EG_RENT",
        law_name_ar="قانون إيجار الأماكن",
        law_number="136 لسنة 1981",
        year=1981,
        pdf_filename="EG_RENT.pdf",
        expected_article_count=80,
        repealed_articles=[],
        expected_chapter_headings=6,
    ),
    "EG_CIVIL_PROCEDURE": LawEntry(
        law_id="EG_CIVIL_PROCEDURE",
        law_name_ar="قانون المرافعات المدنية والتجارية",
        law_number="13 لسنة 1968",
        year=1968,
        pdf_filename="EG_CIVIL_PROCEDURE.pdf",
        expected_article_count=480,
        repealed_articles=[],
        expected_chapter_headings=40,
    ),
    "EG_COMMERCIAL": LawEntry(
        law_id="EG_COMMERCIAL",
        law_name_ar="قانون التجارة",
        law_number="17 لسنة 1999",
        year=1999,
        pdf_filename="EG_COMMERCIAL.pdf",
        expected_article_count=700,
        repealed_articles=[],
        expected_chapter_headings=60,
    ),
    "EG_CIVIL_CODE": LawEntry(
        law_id="EG_CIVIL_CODE",
        law_name_ar="القانون المدني المصري",
        law_number="131 لسنة 1948",
        year=1948,
        pdf_filename="EG_CIVIL_CODE.pdf",
        expected_article_count=686,
        repealed_articles=[],
        expected_chapter_headings=55,
    ),
    "EG_PENAL": LawEntry(
        law_id="EG_PENAL",
        law_name_ar="قانون العقوبات",
        law_number="58 لسنة 1937",
        year=1937,
        pdf_filename="EG_PENAL.pdf",
        expected_article_count=535,
        repealed_articles=[],
        expected_chapter_headings=45,
    ),
    "EG_IP": LawEntry(
        law_id="EG_IP",
        law_name_ar="قانون حماية حقوق الملكية الفكرية",
        law_number="82 لسنة 2002",
        year=2002,
        pdf_filename="EG_IP.pdf",
        expected_article_count=188,
        repealed_articles=[],
        expected_chapter_headings=18,
    ),
}


def get_law(law_id: str) -> LawEntry:
    if law_id not in LAW_REGISTRY:
        raise KeyError(f"Law ID '{law_id}' not found in registry. Available: {list(LAW_REGISTRY.keys())}")
    return LAW_REGISTRY[law_id]
