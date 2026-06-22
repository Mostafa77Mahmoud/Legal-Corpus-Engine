# Nezam Legal Corpus — دليل Pipeline الشامل
# دورة حياة القانون من الرفع حتى التصدير النهائي

**آخر تحديث:** يونيو 2026  
**الحالة الحالية:** Stage 1 → Stage 4 مكتملة ✅ | Stage 5 → Post-Export قيد التخطيط ⏳

---

## فهرس المحتويات

1. [نظرة عامة على Pipeline](#1-نظرة-عامة-على-pipeline)
2. [كيفية إضافة قانون جديد](#2-كيفية-إضافة-قانون-جديد)
3. [دورة حياة القانون — رسم بياني](#3-دورة-حياة-القانون--رسم-بياني)
4. [المسارات الثلاثة للمدخلات](#4-المسارات-الثلاثة-للمدخلات)
5. [Stage 1: Raw Extraction](#5-stage-1--raw-extraction-)
6. [Stage 1.3: Arabic Cleanup](#6-stage-13--arabic-cleanup-)
7. [Stage 1.5: Confidence Scoring](#7-stage-15--confidence-scoring-)
8. [Stage 2: Article Splitting](#8-stage-2--article-splitting-)
9. [Stage 2.5: Split Validation](#9-stage-25--split-validation-)
10. [Stage 3: Metadata Enrichment](#10-stage-3--metadata-enrichment-)
11. [Stage 3.7: Chunking](#11-stage-37--chunking-)
12. [Stage 4: Human Review Export](#12-stage-4--human-review-export-)
13. [Stage 5: Final Validation ⏳](#13-stage-5--final-validation-)
14. [Stage 6: Assembly ⏳](#14-stage-6--assembly-)
15. [Stage 7: MongoDB + JSON Export ⏳](#15-stage-7--mongodb--json-export-)
16. [Post-Export: Embeddings ⏳](#16-post-export--embeddings-)
17. [القوانين المسجلة](#17-القوانين-المسجلة)
18. [بنية الملفات الكاملة](#18-بنية-الملفات-الكاملة)
19. [جداول التكلفة والوقت](#19-جداول-التكلفة-والوقت)

---

## 1. نظرة عامة على Pipeline

**Nezam Legal Corpus** هو pipeline أوتوماتيكي لبناء corpus قانوني مصري رقمي عالي الجودة. المدخل: ملفات PDF أو TXT للقوانين المصرية. المخرج: بيانات قانونية منظمة جاهزة لتشغيل تطبيقات الذكاء الاصطناعي القانوني.

```
مدخل: PDF أو TXT
         │
         ▼
  9 مراحل معالجة
         │
         ▼
مخرج: MongoDB + JSON + Embeddings
      جاهز للـ RAG / Legal AI
```

**لماذا 9 مراحل؟**  
القوانين المصرية تأتي بجودات مختلفة جداً — من PDF رقمي نظيف إلى PDF ممسوح ضوئياً بعيوب ترميز عربي. كل مرحلة تحل مشكلة محددة وتتحقق من جودة مخرجها قبل تمرير البيانات للمرحلة التالية.

---

## 2. كيفية إضافة قانون جديد

### الخطوة 1: سجّل القانون في `config/law_registry.py`

```python
"EG_NEW_LAW": LawEntry(
    law_id="EG_NEW_LAW",
    law_name_ar="اسم القانون بالعربية",
    law_number="رقم القانون",
    year=2024,
    pdf_filename="EG_NEW_LAW.pdf",      # اسم ملف الـ PDF
    txt_filename="EG_NEW_LAW.txt",       # اختياري — إذا لديك TXT جاهز
    expected_article_count=50,           # عدد المواد المتوقع (يشمل مواد الإصدار)
    repealed_articles=[],                # أرقام المواد الملغاة إن وجدت
    expected_chapter_headings=10,        # عدد الفصول/الأبواب المتوقعة
    notes="ملاحظات أي شيء مهم عن هذا القانون",
)
```

### الخطوة 2: ضع الملف في المجلد الصحيح

| نوع الملف | المجلد |
|-----------|--------|
| PDF | `data/raw_pdfs/EG_NEW_LAW.pdf` |
| TXT (اختياري) | `data/raw_txts/EG_NEW_LAW.txt` |

> **ملاحظة:** إذا سجّلت `txt_filename`، سيستخدم Pipeline الـ TXT تلقائياً ويتجاهل الـ PDF في Stage 1. لكن احتفظ بالـ PDF في المجلد لأن Stage 1 يحتاج مسار الـ PDF للميتاداتا.

### الخطوة 3: شغّل Pipeline

```bash
cd nezam-legal-corpus
python run_pilot.py EG_NEW_LAW
```

يمر القانون تلقائياً بجميع المراحل من 1 إلى 4.

---

## 3. دورة حياة القانون — رسم بياني

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     رفع الملف                                           │
│           data/raw_pdfs/   أو   data/raw_txts/                          │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │  هل يوجد txt_filename?  │
          └────────────┬────────────┘
          لا ↙                    ↘ نعم
    ┌──────────────┐         ┌──────────────────┐
    │  قراءة PDF   │         │  قراءة TXT مباشرة│
    │  بـ PyMuPDF  │         │  (بدون Gemini)   │
    └──────┬───────┘         └────────┬─────────┘
           │                          │
    ┌──────┴────────────────────────┐ │
    │  هل النص مقروء؟               │ │
    │  (Arabic density ≥ 0.4?)      │ │
    └──────┬──────────────┬─────────┘ │
   نعم ↙               ↘ لا          │
 ┌─────────┐      ┌────────────────┐  │
 │ PyMuPDF │      │  Gemini OCR    │  │
 │ ✓ مجاني │      │ (File API)     │  │
 └────┬────┘      │ ~ $0.01/قانون  │  │
      │           └───────┬────────┘  │
      │                   │           │
      └─────────┬─────────┘           │
                └──────────┬──────────┘
                           │
                ┌──────────▼───────────┐
                │  Stage 1 — مكتملة   │
                │  data/extracted_raw/ │
                └──────────┬───────────┘
                           │
                ┌──────────▼───────────┐
                │  Stage 1.3 — تنظيف  │
                │ إزالة التشكيل/الزخارف│
                │ data/extracted_clean/│
                └──────────┬───────────┘
                           │
                ┌──────────▼──────────────────┐
                │  Stage 1.5 — Confidence     │
                │  5 عوامل → درجة ≥ 0.85     │
                │  ✗ فشل → توقف للمراجعة     │
                └──────────┬──────────────────┘
                           │ نجاح
                ┌──────────▼──────────────────┐
                │  Stage 2 — تقسيم المواد     │
                │  Regex → LLM fallback        │
                │  data/split_articles/        │
                └──────────┬──────────────────┘
                           │
                ┌──────────▼──────────────────┐
                │  Stage 2.5 — التحقق         │
                │  6 أكواد خطأ               │
                │  ✗ فشل → توقف للمراجعة     │
                └──────────┬──────────────────┘
                           │ نجاح
                ┌──────────▼──────────────────┐
                │  Stage 3 — Enrichment       │
                │  Gemini: topic/keywords/    │
                │  summary per article        │
                │  data/enriched_articles/    │
                └──────────┬──────────────────┘
                           │
                ┌──────────▼──────────────────┐
                │  Stage 3.7 — Chunking       │
                │  تقسيم المواد الكبيرة       │
                │  data/chunks/               │
                └──────────┬──────────────────┘
                           │
                ┌──────────▼──────────────────┐
                │  Stage 4 — Human Review     │ ← نحن هنا الآن ✅
                │  JSON + CSV للمراجعة البشرية│
                │  data/human_review/         │
                └──────────┬──────────────────┘
                           │ (بعد المراجعة البشرية)
                ┌──────────▼──────────────────┐
                │  Stage 5 — Validation ⏳    │
                │  قواعد + LLM re-check       │
                └──────────┬──────────────────┘
                           │
                ┌──────────▼──────────────────┐
                │  Stage 6 — Assembly ⏳      │
                │  dedup + is_repealed        │
                └──────────┬──────────────────┘
                           │
                ┌──────────▼──────────────────┐
                │  Stage 7 — Export ⏳        │
                │  MongoDB + JSON             │
                └──────────┬──────────────────┘
                           │
                ┌──────────▼──────────────────┐
                │  Post-Export — Embeddings ⏳│
                │  text-embedding-004         │
                └─────────────────────────────┘
```

---

## 4. المسارات الثلاثة للمدخلات

### المسار أ — TXT جاهز (الأسرع، بدون تكلفة API)

```
مثال: EG_PDPL (قانون حماية البيانات)
الملف: data/raw_txts/EG_PDPL.txt

Stage 1:
  ① قراءة TXT مباشرة
  ② تنظيف رأس الصفحة (masaar.net boilerplate)
  ③ الحفظ بمصدر: "plaintext"
  التكلفة: $0.00 | الوقت: < 1 ثانية
```

**متى يُستخدم؟** عندما يوفر المصدر (مثل masaar.net) النص كاملاً بشكل مباشر.

---

### المسار ب — PDF رقمي (fast, مجاني في الغالب)

```
مثال: معظم القوانين الحديثة
الملف: data/raw_pdfs/EG_EVIDENCE.pdf

Stage 1:
  ① PyMuPDF يستخرج النص من الـ PDF
  ② فحص أولي سريع للجودة
  ③ إذا Arabic density ≥ 0.4 AND chars ≥ 200 → مقبول
  ④ الحفظ بمصدر: "pymupdf"
  التكلفة: $0.00 | الوقت: 1-5 ثواني
```

**متى يُستخدم؟** PDF رقمي حديث لم يُطبع ويُمسح.

---

### المسار ج — PDF ممسوح أو به عيوب ترميز (OCR required)

```
مثال: EG_ESIGN (عيوب ligature في الترميز العربي)
      EG_PENAL, EG_RENT (مسوحات ضوئية قديمة)
الملف: data/raw_pdfs/EG_ESIGN.pdf

Stage 1:
  ① PyMuPDF يحاول الاستخراج → النص مشوّه/فارغ
  ② فحص الجودة: Arabic density < threshold → فشل
  ③ تلقائياً: Gemini OCR (File API)
     - رفع الـ PDF لـ Gemini File API
     - Prompt متخصص للنصوص القانونية العربية
     - استلام النص كـ Markdown
  ④ الحفظ بمصدر: "gemini_ocr" + اسم الموديل
  التكلفة: ~$0.003/صفحة | الوقت: 30-120 ثانية
```

**متى يُستخدم؟** PDFs قديمة أو مسوحة أو بها عيوب ترميز.

---

## 5. Stage 1 — Raw Extraction ✅

**الملف:** `pipeline/stage_1_extract.py`  
**المدخل:** `data/raw_pdfs/{LAW_ID}.pdf` أو `data/raw_txts/{LAW_ID}.txt`  
**المخرج:**
- `data/extracted_raw/{LAW_ID}.txt` — النص الخام المستخرج
- `data/extracted_raw/{LAW_ID}_meta.json` — ميتاداتا الاستخراج

### ما يحدث بالتفصيل

```
1. تحديد مصدر المدخل:
   - إذا law_entry.txt_filename موجود → TXT mode
   - وإلا → PDF mode

2. TXT mode:
   - قراءة الملف بـ UTF-8
   - strip_txt_boilerplate() → حذف رأسيات موقع masaar.net
   - الحفظ بمصدر "plaintext"

3. PDF mode — محاولة PyMuPDF:
   - fitz.open() → page.get_text("text") لكل صفحة
   - فحص سريع: len(text) < 200 chars → مباشرة للـ OCR
   - حساب arabic_density = عدد الحروف العربية / إجمالي الحروف
   - إذا density >= CONFIDENCE_THRESHOLD → قبول PyMuPDF
   - إذا density < threshold → تحويل للـ OCR

4. Gemini OCR (عند الحاجة):
   - رفع الـ PDF للـ Gemini File API (مؤقت، 48 ساعة)
   - إرسال OCR_PROMPT المتخصص
   - انتظار الاستجابة (مع retry logic)
   - الحفظ بمصدر "gemini_ocr"

5. الكتابة:
   - النص → extracted_raw/{LAW_ID}.txt
   - الميتاداتا → extracted_raw/{LAW_ID}_meta.json
```

### مخرجات الميتاداتا

```json
{
  "law_id": "EG_PDPL",
  "extraction_source": "plaintext",      // pymupdf | gemini_ocr | plaintext
  "char_count": 37003,
  "page_count": 15,
  "arabic_density": 0.9206,
  "replacement_density": 0.000002,
  "article_markers_found": 56,
  "structural_headings_found": 14,
  "extraction_model": null,              // اسم Gemini model إذا استخدم OCR
  "extraction_date": "2026-06-21T..."
}
```

### بوابة الجودة

لا توجد بوابة توقف في Stage 1 — يمرر النص دائماً للمرحلة التالية. التحقق الصارم يحدث في Stage 1.5.

---

## 6. Stage 1.3 — Arabic Cleanup ✅

**الملف:** `pipeline/stage_1_3_cleanup.py`  
**المدخل:** `data/extracted_raw/{LAW_ID}.txt`  
**المخرج:**
- `data/extracted_clean/{LAW_ID}.txt` — النص المنظّف
- `data/cleanup_audit_logs/{LAW_ID}_cleanup_audit.json` — سجل التغييرات

### ما يحدث بالتفصيل

```
8 تحويلات مرتبة:

1. NFC Unicode Normalization
   قبل: أ (U+0623 + U+0648)  →  بعد: أ (U+0623 composed)
   لماذا: منع تكرار الكلمات نفسها بترميز مختلف في البحث

2. إزالة Tatweel (U+0640)
   قبل: "بـيـانـات"  →  بعد: "بيانات"
   لماذا: التطويل التجميلي يكسر الـ tokenization

3. إزالة التشكيل (U+064B–U+065F, U+0670)
   قبل: "البَيَانَاتِ"  →  بعد: "البيانات"
   لماذا: القوانين الرسمية لا تُشكَّل، والتشكيل العشوائي يضر جودة الـ embedding

4. تنظيع الهمزة
   قبل: إ أ آ ا  →  بعد: ا (consistent)
   ملاحظة: يطبّق فقط على حالات الـ OCR الواضحة، لا يغير الهمزات الجوهرية

5. تنظيع الياء
   قبل: ى ي  →  بعد: ي
   لماذا: ألف مقصورة vs ياء — مشكلة شائعة جداً في الـ OCR المصري

6. إزالة Control Characters (U+0000–U+0008, etc.)
   لماذا: بقايا PDF encoding تكسر التعابير النمطية لاحقاً

7. تقليص المسافات المتكررة
   قبل: "البيانات    الشخصية"  →  بعد: "البيانات الشخصية"

8. تقليص الأسطر الفارغة المتكررة
   قبل: 3+ أسطر فارغة  →  بعد: سطران فارغان
   لماذا: يحافظ على بنية الفقرات للـ chunking لاحقاً
```

### سجل التدقيق

```json
{
  "law_id": "EG_PDPL",
  "chars_before": 37003,
  "chars_after": 36283,
  "chars_removed": 720,
  "nfc_changed": 0,
  "tatweel_removed": 12,
  "diacritics_removed": 145,
  "hamza_normalised": 203,
  "yeh_normalised": 89,
  "control_removed": 5,
  "spaces_collapsed": 201,
  "newlines_collapsed": 65,
  "cleaned_at": "2026-06-21T..."
}
```

**لا توجد بوابة توقف** — Stage 1.3 تُنظّف فقط ولا تتوقف أبداً.

---

## 7. Stage 1.5 — Confidence Scoring ✅

**الملف:** `pipeline/stage_1_5_val_extract.py`  
**المدخل:** `data/extracted_clean/{LAW_ID}.txt`  
**المخرج:** `data/extracted_raw/{LAW_ID}_confidence.json`

### نموذج الجودة — 5 عوامل مرجّحة

| العامل | الوزن | كيف يُحسب | لماذا |
|--------|-------|-----------|-------|
| `arabic_density` | 0.35 | نسبة الحروف العربية للإجمالي | النص العربي الأصيل vs OCR فاشل |
| `article_marker_density` | 0.30 | عدد علامات "مادة" / العدد المتوقع | هل وُجدت جميع المواد؟ |
| `structural_headings` | 0.15 | عدد الفصول/الأبواب / المتوقع | هل بنية القانون محفوظة؟ |
| `char_count_ratio` | 0.15 | عدد الأحرف / الحد الأدنى المتوقع | هل النص مكتمل؟ |
| `replacement_char_density` | 0.05 | نسبة ؟ (U+FFFD) بالعكس | هل يوجد نص تالف؟ |

**المعادلة:**
```
confidence = Σ (factor_normalized × weight)
```

**بوابة الجودة:**
```
≥ 0.85 → PASS — يستمر Pipeline
< 0.85 → FAIL — توقف كامل + طلب مراجعة بشرية
```

### مثال مخرج

```json
{
  "law_id": "EG_PDPL",
  "confidence_score": 0.9206,
  "threshold": 0.85,
  "passed": true,
  "manual_review": false,
  "factor_breakdown": {
    "arabic_density":        {"raw": 0.94, "norm": 1.0, "weight": 0.35, "contribution": 0.35},
    "article_marker_density":{"raw": 1.00, "norm": 1.0, "weight": 0.30, "contribution": 0.30},
    "structural_headings":   {"raw": 0.85, "norm": 0.85,"weight": 0.15, "contribution": 0.13},
    "char_count_ratio":      {"raw": 0.92, "norm": 0.92,"weight": 0.15, "contribution": 0.14},
    "replacement_char_density":{"raw":0.0, "norm": 1.0, "weight": 0.05, "contribution": 0.05}
  }
}
```

---

## 8. Stage 2 — Article Splitting ✅

**الملف:** `pipeline/stage_2_split.py`  
**المدخل:** `data/extracted_clean/{LAW_ID}.txt`  
**المخرج:** `data/split_articles/{LAW_ID}/articles.json`

### استراتيجية التقسيم: Regex أولاً → LLM fallback

```
1. تطبيق الـ Regex patterns للبحث عن حدود المواد
2. تشغيل Stage 2.5 validation على النتيجة
3. إذا PASS → اكتمل
4. إذا FAIL → إرسال الأقسام الإشكالية لـ Gemini للإعادة
5. إعادة التحقق → إذا فشل → توقف للمراجعة البشرية
```

### أنماط المواد المدعومة

| النمط | مثال | نوعه |
|-------|------|-------|
| `مادة (١)` أو `مادة (1)` | مادة (٥) | main — رقمي بأقواس |
| `مادة 5` أو `المادة 5` | المادة ٥٠ | main — رقمي مباشر |
| `(المادة الأولى)` .. `(المادة السابعة)` | (المادة الثانية) | issuance — ترتيبي |

### بنية كل مادة مُقسَّمة

```json
{
  "article_id": "EG_PDPL_008",
  "law_id": "EG_PDPL",
  "article_number": 8,
  "article_number_raw": "مادة (٨)",
  "article_type": "main",          // main | issuance
  "text": "نص المادة كاملاً...",
  "is_repealed": false,
  "sequence_index": 8,
  "marker_kind": "paren_digit",    // نوع الـ regex الذي التقطه
  "split_source": "regex",         // regex | gemini_llm
  "word_count": 45,
  "char_count": 280
}
```

---

## 9. Stage 2.5 — Split Validation ✅

**الملف:** `pipeline/stage_2_5_val_split.py`  
**المدخل:** مخرج Stage 2 (articles list)  
**المخرج:** `data/split_articles/{LAW_ID}/validation_report.json`

### تصنيف الأخطاء — 6 أكواد

| الكود | الاسم | التعريف | التأثير |
|-------|-------|---------|---------|
| `E001` | MISSING_ARTICLE | رقم مادة في التسلسل غير موجود | خطأ — يوقف Pipeline |
| `E002` | DUPLICATE_ARTICLE | نفس رقم المادة ظهر مرتين | خطأ — يوقف Pipeline |
| `E003` | SEQUENCE_GAP | فجوة في التسلسل بدون تسجيل إلغاء | تحذير — مقبول في حدود |
| `E004` | EMPTY_BODY | مادة بها رقم لكن بدون نص | خطأ — يوقف Pipeline |
| `E005` | OVERSIZED_ARTICLE | نص المادة > 3× متوسط المواد | تحذير — طبيعي لمواد التعريف |
| `E006` | ORPHAN_TEXT | نص بين مادتين غير محسوب | تحذير — مقبول في حدود |

### بوابة الجودة

```
أي E001, E002, E004 → FAIL → توقف Pipeline
E003, E005, E006 فقط → PASS مع warnings
```

---

## 10. Stage 3 — Metadata Enrichment ✅

**الملف:** `pipeline/stage_3_enrich.py`  
**المدخل:** `data/split_articles/{LAW_ID}/articles.json`  
**المخرج:** `data/enriched_articles/{LAW_ID}/articles.json`

### ما يحدث لكل مادة

```
لكل مادة في القانون:
  1. فحص الـ cache: هل هذه المادة مُخصَّبة مسبقاً؟
     → نعم: تخطّ (لا Gemini call)
     → لا: أرسل للـ Gemini

  2. بناء الـ prompt:
     - اسم القانون
     - معرّف المادة (EG_PDPL_008)
     - نوع المادة (issuance/main)
     - النص الكامل

  3. Gemini يعيد JSON بـ 5 حقول:
     - topic: موضوع المادة (2-5 كلمات)
     - keywords: 3-8 مصطلحات قانونية
     - article_summary: ملخص الجملة
     - article_category: تعريف | حق | التزام | إجراء | عقوبة | تنظيمية | انتقالية | إصدار | أخرى
     - legal_entities: الجهات القانونية المذكورة

  4. دمج مع بيانات المادة الأصلية + حفظ
```

### مثال مادة مُخصَّبة

```json
{
  "article_id": "EG_PDPL_008",
  "law_id": "EG_PDPL",
  "article_number": 8,
  "article_type": "main",
  "text": "يلتزم المتحكم في البيانات بالحصول على موافقة صريحة...",
  "topic": "موافقة صاحب البيانات",
  "keywords": ["موافقة صريحة", "متحكم", "بيانات شخصية", "حقوق صاحب البيانات"],
  "article_summary": "يوجب الحصول على موافقة صريحة من صاحب البيانات قبل معالجتها.",
  "article_category": "التزام",
  "legal_entities": ["المتحكم في البيانات", "صاحب البيانات"],
  "enrichment_model": "gemini-3.5-flash",
  "enrichment_error": null
}
```

### تفاصيل التكلفة

| القانون | المواد | Gemini Calls | التكلفة التقديرية |
|---------|--------|-------------|-----------------|
| EG_PDPL | 56 | 56 | ~$0.003 |
| EG_ESIGN | 30 | 30 | ~$0.002 |
| EG_LABOR | 254 | 254 | ~$0.015 |
| EG_CIVIL_CODE | 686 | 686 | ~$0.040 |

**ملاحظة:** كل مادة = 1 Gemini call. مع 4 مفاتيح مجانية = 80 call/يوم. EG_CIVIL_CODE يحتاج ~9 أيام على المجاني.

---

## 11. Stage 3.7 — Chunking ✅

**الملف:** `pipeline/stage_3_7_chunk.py`  
**المدخل:** `data/enriched_articles/{LAW_ID}/articles.json`  
**المخرج:**
- `data/chunks/{LAW_ID}/chunks.json`
- `data/chunks/{LAW_ID}/chunking_report.json`

### قواعد التقسيم

```
لكل مادة:
  1. إذا عدد الكلمات ≤ 250 → chunk واحد (لا تقسيم)

  2. إذا > 250 كلمة:
     أ. تقسيم على حدود الفقرات (سطران فارغان \n\n)
     ب. إذا فقرة واحدة > 250 كلمة → تقسيم على حدود الجمل (. أو ؟ أو !)
     ج. دمج الفقرات القصيرة المتجاورة حتى الحد الأقصى

  3. إضافة Overlap Window (30 كلمة):
     - كل chunk (ما عدا الأول) يبدأ بـ 30 كلمة من نهاية الـ chunk السابق
     - لماذا: تحسين جودة الاسترجاع في الـ RAG (السياق لا ينقطع)
```

### مثال تقسيم

```
مادة طويلة (320 كلمة) → 3 chunks:
  EG_PDPL_002_C001  (220 كلمة, has_overlap=False)
  EG_PDPL_002_C002  (220 كلمة, has_overlap=True ← يبدأ بآخر 30 كلمة من C001)
  EG_PDPL_002_C003  (130 كلمة, has_overlap=True)
```

### نتائج EG_PDPL

```
56 مادة → 61 chunk
4 مواد كبيرة انقسمت
متوسط الكلمات/chunk: 97.5
```

---

## 12. Stage 4 — Human Review Export ✅

**الملف:** `pipeline/stage_4_human_review.py`  
**المدخل:**
- `data/enriched_articles/{LAW_ID}/articles.json`
- `data/chunks/{LAW_ID}/chunks.json`

**المخرج:** كل الملفات في `data/human_review/{LAW_ID}/`

| الملف | الحجم (EG_PDPL) | الاستخدام |
|-------|----------------|-----------|
| `articles_review.json` | 120 KB | مراجعة كاملة بالـ metadata |
| `articles_review.csv` | 98 KB | فتح في Excel/Numbers |
| `chunks_review.json` | 108 KB | مراجعة الـ chunks |
| `chunks_review.csv` | 85 KB | فتح في Excel/Numbers |
| `review_manifest.json` | 1.4 KB | ملخص + تعليمات |

### حقول المراجعة المُضافة لكل سجل

```json
"review_status": "",   ← يملؤها الفريق: approved | needs_edit | rejected
"review_notes":  ""    ← ملاحظات حرة
```

### ما يجب التحقق منه أثناء المراجعة

1. **topic** — هل يعكس موضوع المادة الرئيسي بدقة؟
2. **keywords** — هل مكتملة وغير مكررة وذات صلة؟
3. **article_category** — هل يتطابق مع الفصل/الباب الذي تنتمي إليه المادة؟
4. **article_summary** — هل دقيق وغير مضلل؟
5. **legal_entities** — هل تشمل جميع الجهات المذكورة؟
6. **chunk text** — هل كل chunk مفهوم بشكل مستقل؟

---

## 13. Stage 5 — Final Validation ⏳

**الملف:** `pipeline/stage_5_validate.py` *(لم يُبنَ بعد)*  
**المدخل:** `data/human_review/{LAW_ID}/` (بعد اكتمال المراجعة البشرية)  
**المخرج:** `data/validated/{LAW_ID}/articles_validated.json`

### ما سيحدث

```
1. قراءة ملفات المراجعة البشرية
2. رفض أي سجل review_status = "rejected"
3. تعليم السجلات needs_edit للإعادة

4. التحقق من Cross-References (rule-based):
   - كل مرجع صريح (مادة X من قانون Y) يجب أن يحل إلى:
     a. law_id موجود في law_registry
     b. article_number في نطاق المواد المعروفة
   - مراجع غير محلولة → LLM re-validation

5. LLM re-validation (فقط للمراجع غير المحلولة):
   - إرسال نص المادة + المرجع المشكوك فيه لـ Gemini
   - Gemini يقرر: مرجع صحيح | خطأ في الاستخراج | مرجع غير مباشر

6. تقرير نهائي:
   - approved_count, rejected_count, cross_ref_errors
```

**بوابة الجودة:** أي cross-reference error غير محلول → يوقف تقدم القانون لـ Stage 6.

---

## 14. Stage 6 — Assembly ⏳

**الملف:** `pipeline/stage_6_assemble.py` *(لم يُبنَ بعد)*  
**المدخل:** `data/validated/{LAW_ID}/`  
**المخرج:** `data/assembled/{LAW_ID}/`

### ما سيحدث

```
1. Deduplication:
   - إذا نفس القانون تمت معالجته مرتين (نادر) → الاحتفاظ بالأحدث
   - تعريف التكرار: (law_id + article_number) فريد

2. is_repealed propagation:
   - قراءة law_entry.repealed_articles
   - تحديث is_repealed=true لجميع المواد المسجلة كملغاة

3. is_current_version:
   - إذا توجد نسختان من نفس القانون (مثل تعديل 2023 لـ PDPL)
   - تعليم الأحدث بـ is_current_version=true

4. حساب الإحصائيات النهائية:
   - article_count, chunk_count, enrichment_coverage
   - حجم corpus هذا القانون بالكيلوبايت
```

---

## 15. Stage 7 — MongoDB + JSON Export ⏳

**الملف:** `pipeline/stage_7_export.py` *(لم يُبنَ بعد)*  
**المدخل:** `data/assembled/{LAW_ID}/`  
**المخرج:**

```
MongoDB:
  Collection: egyptian_law_articles  ← وثيقة لكل مادة
  Collection: egyptian_law_chunks    ← وثيقة لكل chunk

ملفات:
  data/releases/{LAW_ID}/articles.json
  data/releases/{LAW_ID}/chunks.json
  data/releases/{LAW_ID}/chunks.jsonl   ← للـ embedding pipeline
  data/releases/{LAW_ID}/release_metadata.json
```

### مخطط MongoDB

#### `egyptian_law_articles`
```json
{
  "_id": "EG_PDPL_008",
  "law_id": "EG_PDPL",
  "law_name_ar": "قانون حماية البيانات الشخصية",
  "law_number": "151 لسنة 2020",
  "year": 2020,
  "article_number": 8,
  "article_type": "main",
  "article_category": "التزام",
  "topic": "موافقة صاحب البيانات",
  "keywords": ["موافقة صريحة", "متحكم", "بيانات شخصية"],
  "legal_entities": ["المتحكم في البيانات", "صاحب البيانات"],
  "article_summary": "...",
  "is_repealed": false,
  "is_current_version": true,
  "text": "...",
  "word_count": 45,
  "char_count": 280,
  "chunk_count": 1,
  "processed_at": "2026-06-21T..."
}
```

#### `egyptian_law_chunks`
```json
{
  "_id": "EG_PDPL_008_C001",
  "article_id": "EG_PDPL_008",
  "law_id": "EG_PDPL",
  "chunk_index": 0,
  "chunk_total": 1,
  "text": "...",
  "word_count": 45,
  "has_overlap": false,
  "topic": "موافقة صاحب البيانات",
  "keywords": ["موافقة صريحة", "متحكم"],
  "embedding": null   ← يُملأ في Post-Export
}
```

### Indexes المطلوبة

```javascript
// egyptian_law_articles
db.egyptian_law_articles.createIndex({ law_id: 1, article_number: 1 }, { unique: true })
db.egyptian_law_articles.createIndex({ article_category: 1 })
db.egyptian_law_articles.createIndex({ keywords: 1 })

// egyptian_law_chunks
db.egyptian_law_chunks.createIndex({ article_id: 1 })
db.egyptian_law_chunks.createIndex({ law_id: 1 })
```

### `release_metadata.json`

```json
{
  "law_id": "EG_PDPL",
  "law_name_ar": "قانون حماية البيانات الشخصية",
  "release_version": "1.0.0",
  "article_count": 56,
  "chunk_count": 61,
  "enrichment_coverage": "56/56",
  "extraction_source": "plaintext",
  "confidence_score": 0.9206,
  "processing_date": "2026-06-21",
  "pipeline_stages_completed": ["1","1.3","1.5","2","2.5","3","3.7","4","5","6","7"]
}
```

---

## 16. Post-Export — Embeddings ⏳

**الملف:** `scripts/generate_embeddings.py` *(لم يُبنَ بعد)*  
**المدخل:** `data/releases/{LAW_ID}/chunks.jsonl`  
**المخرج:** تحديث حقل `embedding` في كل وثيقة في MongoDB

### ما سيحدث

```
1. قراءة كل chunk من الـ JSONL
2. إرسال chunk.text لـ text-embedding-004
3. استلام vector بطول 768
4. تحديث MongoDB:
   db.egyptian_law_chunks.updateOne(
     { _id: chunk_id },
     { $set: { embedding: [0.123, -0.456, ...] } }
   )
5. إنشاء Vector Index في MongoDB Atlas:
   للبحث الدلالي بين chunks القانونية
```

### التكلفة

```
text-embedding-004: ~$0.00001 / 1000 tokens
61 chunks (EG_PDPL) ≈ 6100 tokens ≈ $0.00006 (أقل من سنت!)
686 مادة (EG_CIVIL_CODE × avg 5 chunks) ≈ $0.0003
```

---

## 17. القوانين المسجلة

| الترتيب | المعرّف | القانون | المواد | نوع الـ PDF | الحالة |
|---------|---------|---------|--------|------------|--------|
| 1 | EG_PDPL | حماية البيانات الشخصية (151/2020) | 56 | TXT + Digital | ✅ Stage 4 مكتملة |
| 2 | EG_ESIGN | التوقيع الإلكتروني (15/2004) | 30 | OCR (ligature defect) | ✅ Stage 4 مكتملة |
| 3 | EG_EVIDENCE | الإثبات (25/1968) | 99 | Mixed | ⏳ التالي |
| 4 | EG_LABOR | قانون العمل (12/2003) | 254 | Mixed | ⏳ قيد الانتظار |
| 5 | EG_RENT | إيجار الأماكن (136/1981) | 80 | Scanned | ⏳ قيد الانتظار |
| 6 | EG_CIVIL_PROCEDURE | المرافعات (13/1968) | 480 | Mixed | ⏳ قيد الانتظار |
| 7 | EG_COMMERCIAL | قانون التجارة (17/1999) | 700 | Mixed | ⏳ قيد الانتظار |
| 8 | EG_CIVIL_CODE | القانون المدني (131/1948) | 686 | Mixed | ⏳ آخر الأولوية |
| 9 | EG_PENAL | قانون العقوبات (58/1937) | 535 | Scanned | ⏳ قيد الانتظار |
| 10 | EG_IP | الملكية الفكرية (82/2002) | 188 | Digital | ⏳ قيد الانتظار |

> **قاعدة مهمة:** لا تشغّل EG_CIVIL_CODE حتى تنجح 4 قوانين على الأقل شاملة قانوناً ممسوحاً (EG_RENT).

---

## 18. بنية الملفات الكاملة

```
nezam-legal-corpus/
│
├── config/
│   ├── law_registry.py        # سجل القوانين العشرة
│   ├── settings.py            # المسارات + API keys + نماذج Gemini
│   └── taxonomy.py            # تصنيفات المفاهيم القانونية
│
├── data/
│   ├── raw_pdfs/              # ← ضع PDFs هنا
│   │   └── EG_PDPL.pdf
│   ├── raw_txts/              # ← ضع TXTs هنا
│   │   └── EG_PDPL.txt
│   ├── extracted_raw/         # مخرج Stage 1
│   │   ├── EG_PDPL.txt
│   │   ├── EG_PDPL_meta.json
│   │   └── EG_PDPL_confidence.json
│   ├── extracted_clean/       # مخرج Stage 1.3
│   │   └── EG_PDPL.txt
│   ├── cleanup_audit_logs/    # سجلات Stage 1.3
│   │   └── EG_PDPL_cleanup_audit.json
│   ├── split_articles/        # مخرج Stage 2
│   │   └── EG_PDPL/
│   │       ├── articles.json
│   │       └── validation_report.json
│   ├── enriched_articles/     # مخرج Stage 3
│   │   └── EG_PDPL/
│   │       ├── articles.json
│   │       └── enrichment_report.json
│   ├── chunks/                # مخرج Stage 3.7
│   │   └── EG_PDPL/
│   │       ├── chunks.json
│   │       └── chunking_report.json
│   ├── human_review/          # مخرج Stage 4 ← نحن هنا
│   │   └── EG_PDPL/
│   │       ├── articles_review.json
│   │       ├── articles_review.csv
│   │       ├── chunks_review.json
│   │       ├── chunks_review.csv
│   │       └── review_manifest.json
│   ├── validated/             # مخرج Stage 5 ⏳
│   ├── assembled/             # مخرج Stage 6 ⏳
│   └── releases/              # مخرج Stage 7 ⏳
│
├── pipeline/
│   ├── stage_1_extract.py         ✅
│   ├── stage_1_3_cleanup.py       ✅
│   ├── stage_1_5_val_extract.py   ✅
│   ├── stage_2_split.py           ✅
│   ├── stage_2_5_val_split.py     ✅
│   ├── stage_3_enrich.py          ✅
│   ├── stage_3_7_chunk.py         ✅
│   ├── stage_4_human_review.py    ✅
│   ├── stage_5_validate.py        ⏳
│   ├── stage_6_assemble.py        ⏳
│   └── stage_7_export.py          ⏳
│
├── scripts/
│   └── generate_embeddings.py     ⏳
│
├── utils/
│   ├── arabic_text.py        # تنظيع العربية + كشف علامات المواد
│   ├── cost_tracker.py       # تتبع التوكنات والتكلفة بالدولار
│   ├── key_manager.py        # إدارة مفاتيح Gemini (RPM/RPD/cooldowns)
│   └── llm_client.py         # Gemini wrapper مع retry logic
│
├── docs/
│   ├── PIPELINE_LIFECYCLE.md  ← هذا الملف
│   ├── 00_PROJECT_OVERVIEW.md
│   ├── 01_STAGE_1_EXTRACTION.md
│   ├── 02_STAGE_1_5_CONFIDENCE.md
│   ├── 03_ROADMAP.md
│   ├── 04_PILOT_RESULTS.md
│   ├── 05_INFRASTRUCTURE.md
│   ├── 06_NEXT_STEPS.md
│   ├── 07_STAGE_1_3_CLEANUP.md
│   ├── 07_STAGE3_METADATA.md
│   ├── 08_STAGE37_CHUNKING.md
│   └── 09_STAGE4_HUMAN_REVIEW.md
│
└── run_pilot.py              # نقطة الدخول الرئيسية
```

---

## 19. جداول التكلفة والوقت

### تكلفة Pipeline لكل قانون (تقديرية)

| Stage | الموديل | Calls (avg) | التكلفة |
|-------|---------|------------|---------|
| Stage 1 (TXT) | — | 0 | $0.000 |
| Stage 1 (PyMuPDF) | — | 0 | $0.000 |
| Stage 1 (OCR) | gemini-3.5-flash | 1 | $0.003–$0.015 |
| Stage 2 (LLM fallback) | gemini-3.5-flash | 0–5 | $0.000–$0.050 |
| Stage 3 (Enrichment) | gemini-3.5-flash | 1/مادة | $0.003–$0.040 |
| Stage 5 (LLM re-check) | gemini-3.5-flash | 0–10 | $0.000–$0.010 |
| Post-Export (Embeddings) | text-embedding-004 | 1/chunk | < $0.001 |

**إجمالي EG_PDPL (56 مادة):** ~$0.003  
**إجمالي EG_CIVIL_CODE (686 مادة):** ~$0.040–$0.060

### وقت المعالجة (تقديري)

| Stage | وقت EG_PDPL | وقت قانون كبير (500 مادة) |
|-------|------------|--------------------------|
| Stage 1 (TXT) | < 1 ث | < 1 ث |
| Stage 1 (OCR) | 30–60 ث | 30–60 ث |
| Stage 1.3 | < 1 ث | < 2 ث |
| Stage 1.5 | < 1 ث | < 1 ث |
| Stage 2 | 2–5 ث | 10–30 ث |
| Stage 2.5 | < 1 ث | < 2 ث |
| Stage 3 | 5–10 دق | 60–120 دق (rate limits) |
| Stage 3.7 | < 1 ث | 2–5 ث |
| Stage 4 | < 1 ث | 2–5 ث |

---

## ملخص حالة المشروع

```
✅ Stage 1    — Raw Extraction          (TXT + PyMuPDF + Gemini OCR)
✅ Stage 1.3  — Arabic Cleanup          (8 تحويلات + audit log)
✅ Stage 1.5  — Confidence Scoring      (5 عوامل، بوابة 0.85)
✅ Stage 2    — Article Splitting       (Regex + LLM fallback)
✅ Stage 2.5  — Split Validation        (6 أكواد خطأ)
✅ Stage 3    — Metadata Enrichment     (Gemini: topic/keywords/summary)
✅ Stage 3.7  — Chunking               (paragraph-first + overlap)
✅ Stage 4    — Human Review Export     (JSON + CSV للمراجعة)
⏳ Stage 5    — Final Validation        (cross-refs + LLM re-check)
⏳ Stage 6    — Assembly               (dedup + is_repealed)
⏳ Stage 7    — MongoDB + JSON Export   (2 collections + JSONL)
⏳ Post       — Embeddings             (text-embedding-004)

اختُبر بنجاح على: EG_PDPL (56 مادة، 61 chunk) + EG_ESIGN (30 مادة)
```
