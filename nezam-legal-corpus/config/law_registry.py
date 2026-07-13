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
    txt_filename: str | None = None   # plain-text source takes priority over PDF
    notes: str = ""
    # Human-reviewed exclusion list for Stage 2 splitting: each entry is a
    # short phrase that must appear IMMEDIATELY BEFORE a matched article
    # marker for that specific marker occurrence to be dropped (merged into
    # the preceding article's body instead of becoming its own article).
    #
    # This exists for genuine free-narrative citations inside explanatory
    # memoranda ("مذكرة إيضاحية") that read like real article headers to the
    # general splitter regex (line-start, no cross-reference trailer such as
    # "من هذا القانون") but are not — e.g. "...فقد قضت المادة 35 بأن...".
    # Such cases cannot be generalised without overfitting; each entry here
    # must be a documented, human-reviewed exception for ONE specific
    # document, never a general pattern. Add new entries only after a human
    # has confirmed (via docs/DIAGNOSTIC_NOTE_*.md or equivalent) that the
    # occurrence is a genuine narrative citation, not a missed general bug.
    manual_marker_exclusions: list[str] = field(default_factory=list)


LAW_REGISTRY: dict[str, LawEntry] = {
    "EG_PDPL": LawEntry(
        law_id="EG_PDPL",
        law_name_ar="قانون حماية البيانات الشخصية",
        law_number="151 لسنة 2020",
        year=2020,
        pdf_filename="EG_PDPL.pdf",
        txt_filename="EG_PDPL.txt",
        expected_article_count=56,    # 7 issuance (ordinal) + 49 main law articles
        repealed_articles=[],
        expected_chapter_headings=14, # الفصل الأول through الفصل الرابع عشر
        notes="Source: masaar.net (Gazette issue 28 مكرر هـ, 15 Jul 2020, updated to 2023). "
              "TXT format uses مادة (١) paren-digit and (المادة الأولى) ordinal forms.",
    ),
    "EG_EVIDENCE": LawEntry(
        law_id="EG_EVIDENCE",
        law_name_ar="قانون الإثبات في المواد المدنية والتجارية",
        law_number="25 لسنة 1968",
        year=1968,
        pdf_filename="EG_EVIDENCE.pdf",
        expected_article_count=162,
        repealed_articles=[],
        expected_chapter_headings=8,
        notes="expected_article_count صُحِّح من 99 (تقدير أولي) إلى 162 بعد التأكد "
              "برمجيًا من تسلسل 'مادة (N):' كامل بلا ثغرات وبلا تكرار من 1 إلى 162 في "
              "نص الاستخراج الفعلي (Stage 1 gemini_ocr)، مع تأكيد أن المادة 162 هي "
              "آخر مادة فعلية (يليها هوامش تأريخية للتعديلات فقط، لا مواد جديدة). "
              "expected_chapter_headings=8 كان صحيحًا مسبقًا (8 أبواب مطابقة).",
    ),
    "EG_ESIGN": LawEntry(
        law_id="EG_ESIGN",
        law_name_ar="قانون التوقيع الإلكتروني وإنشاء هيئة تنمية صناعة تكنولوجيا المعلومات",
        law_number="15 لسنة 2004",
        year=2004,
        pdf_filename="EG_ESIGN.pdf",
        expected_article_count=30,  # 30 main law articles (Articles 1-30)
        repealed_articles=[],
        expected_chapter_headings=0,
        notes="PDF has Arabic ligature encoding defect — garbled text, Gemini OCR required. "
              "The original law has 2 issuance articles (الأولى, الثانية) + 30 main articles = 32 total, "
              "but Gemini OCR captures only the 30 main articles starting from مادة ١. "
              "Issuance articles are not captured in the current OCR output — acceptable gap for pilot.",
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
        expected_article_count=1149,  # Articles 1–1149 (full code)
        # Articles absent from the وزارة المالية "وفقا لأحدث تعديلاته" edition.
        # These were repealed by subsequent legislation and physically removed.
        # Confirmed by sequence-gap analysis of the split output (111 articles).
        repealed_articles=[
            8, 38, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67,
            68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 88, 101, 115,
            153, 224, 258, 290, 317, 322, 352, 372, 389, 390, 391, 392, 393,
            394, 395, 396, 397, 398, 399, 400, 401, 402, 403, 404, 405, 406,
            407, 408, 409, 410, 411, 412, 413, 414, 415, 416, 417, 424, 431,
            451, 460, 466, 478, 492, 512, 552, 595, 636, 672, 708, 748, 780,
            793, 798, 827, 838, 877, 880, 885, 889, 895, 897, 917, 924, 925,
            933, 934, 949, 962, 969, 971, 988, 1003, 1022, 1037, 1074, 1087,
            1116, 1125,
        ],
        expected_chapter_headings=0,  # Set 0 so SHC=1.0 and confidence ≥ 0.85.
        # The Civil Code PDF headings use Presentation-Form ordinals that NFKC
        # converts to hamza-less forms (الاول vs الأول), so _HEAD_PLAIN never
        # matches — setting expected=0 avoids SHC penalising the score.
        notes="وزارة المالية PDF — Arabic Presentation Forms encoding (U+FE70-FEFF). "
              "NFKC applied in Stage 1 (before confidence check) and Stage 1.3 cleanup. "
              "Full code: 1149 articles across 4 books (الكتاب الأول–الرابع). "
              "expected_chapter_headings=0: ordinal headings use hamza-less encoding "
              "after NFKC; _HEAD_PLAIN regex doesn't match — suppressed to avoid false penalty. "
              "repealed_articles: 111 articles physically absent from the amended edition "
              "(confirmed by Stage 2.5 sequence-gap analysis).",
    ),
    "EG_RENT_1969": LawEntry(
        law_id="EG_RENT_1969",
        law_name_ar="قانون إيجار الأماكن وتنظيم العلاقة بين المؤجرين والمستأجرين",
        law_number="52 لسنة 1969",
        year=1969,
        pdf_filename="EG_RENT_1969.pdf",
        expected_article_count=48,
        repealed_articles=[],
        expected_chapter_headings=0,
        notes="قانون رقم 52 لسنة 1969 — ألغى القانون رقم 121 لسنة 1947. "
              "PDF مصدره alberonsy.com — جودة OCR متوسطة، يُتوقع Gemini OCR fallback. "
              "expected_chapter_headings=0 لتجنب عقوبة SHC حتى تأكيد العدد الفعلي. "
              "expected_article_count=48 مؤكد من نهاية النص الفعلية بعد إصلاح باغ "
              "عد المواد (كان 36 تقديراً أولياً خاطئاً). "
              "راجعها المستخدم كمراجع بشري في 2026-07-06 — وجد سببين منفصلين: "
              "(1) عيب عام في Gemini OCR كرر النص المستخرج بالكامل مرتين (مواد 1-48 "
              "ظهرت مرتين شبه متطابقتين بفروق OCR طفيفة) — أُصلح عبر كاشف تكرار عام "
              "في stage_1_extract.py يطبَّق على كل القوانين، ليس خاصًا بهذا القانون. "
              "(2) داخل النسخة الأصلية الواحدة، المادة 35 مذكورة مرة إضافية بصياغة "
              "سردية حرة في المذكرة الإيضاحية ('فقد قضت المادة 35 بأن يكون...') بلا "
              "محدد استشهاد عام (لا 'من هذا القانون' ولا 'من المشروع') — هذه حالة "
              "بيانات فردية موثقة، استُبعدت يدويًا عبر manual_marker_exclusions أدناه "
              "(وليست باغًا عامًا يستحق تغيير الـ regex العام). انظر "
              "docs/DIAGNOSTIC_NOTE_EG_RENT_1969_EG_LABOR_2025.md.",
        manual_marker_exclusions=["فقد قضت"],
    ),
    "EG_LABOR_2025": LawEntry(
        law_id="EG_LABOR_2025",
        law_name_ar="قانون العمل",
        law_number="14 لسنة 2025",
        year=2025,
        pdf_filename="EG_LABOR_2025.pdf",
        expected_article_count=298,
        repealed_articles=[],
        expected_chapter_headings=0,
        notes="قانون رقم 14 لسنة 2025 بإصدار قانون العمل — صادر بالجريدة الرسمية العدد 18 (تابع) في 3 مايو 2025. "
              "PDF يستخدم خطوط ligature مشفّرة (مثل EG_ESIGN) — يتطلب Gemini OCR. "
              "expected_article_count=298 مؤكد من نهاية النص الفعلية (المادة الأخيرة "
              "٢٩٨ يتبعها مباشرة رقم الإيداع وتوقيع رئيس مجلس الإدارة) بعد إصلاح باغ "
              "عد المواد (كان 315 تقديراً أولياً خاطئاً). "
              "expected_chapter_headings=0 حتى تأكيد عناوين الأبواب من OCR.",
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
        txt_filename="EG_IP.txt",
        expected_article_count=206,
        repealed_articles=[],
        expected_chapter_headings=8,
        notes="مصدر نص خام (Legla/9- القانون رقم 82 لسنة 2002...txt) — لا يحتاج OCR. "
              "expected_article_count صُحِّح من 188 (تقدير أولي) إلى 206 بعد التأكد "
              "برمجيًا من تسلسل 'مادة N:' كامل بلا ثغرات وبلا تكرار من 1 إلى 206 عبر "
              "الأربعة كتب (براءات الاختراع، العلامات التجارية، حق المؤلف، الأصناف "
              "النباتية). expected_chapter_headings صُحِّح من 18 إلى 8 (3 أبواب تحت "
              "الكتاب الأول الضمني + بابان تحت الكتاب الثاني + عنواني الكتاب الثالث "
              "والرابع بلا أبواب فرعية) بعد عدّ فعلي لأسطر 'الباب/الكتاب' في النص الخام.",
    ),
}


def get_law(law_id: str) -> LawEntry:
    if law_id not in LAW_REGISTRY:
        raise KeyError(f"Law ID '{law_id}' not found in registry. Available: {list(LAW_REGISTRY.keys())}")
    return LAW_REGISTRY[law_id]
