# Nezam Legal Corpus — Project Status

**آخر تحديث:** يونيو 2026  
**الحالة الإجمالية:** Pipeline كامل (Stages 1→7) ✅ | جاهز لإضافة قوانين جديدة

---

## حالة Pipeline الكاملة

| Stage | الاسم | الحالة | الملف |
|-------|-------|--------|-------|
| **1** | Raw Extraction | ✅ مكتمل + مُختبر | `pipeline/stage_1_extract.py` |
| **1.3** | Arabic Cleanup | ✅ مكتمل + مُختبر | `pipeline/stage_1_3_cleanup.py` |
| **1.5** | Confidence Scoring | ✅ مكتمل + مُختبر | `pipeline/stage_1_5_val_extract.py` |
| **2** | Article Splitting | ✅ مكتمل + مُختبر | `pipeline/stage_2_split.py` |
| **2.5** | Split Validation | ✅ مكتمل + مُختبر | `pipeline/stage_2_5_val_split.py` |
| **3** | Metadata Enrichment | ✅ مكتمل + مُختبر | `pipeline/stage_3_enrich.py` |
| **3.7** | Chunking | ✅ مكتمل + مُختبر | `pipeline/stage_3_7_chunk.py` |
| **4** | Human Review Export | ✅ مكتمل + مُختبر | `pipeline/stage_4_human_review.py` |
| **5** | Corpus Validation | ✅ مكتمل + مُختبر | `pipeline/stage_5_validate.py` |
| **6** | Assembly | ✅ مكتمل + مُختبر | `pipeline/stage_6_assemble.py` |
| **7** | JSON Export | ✅ مكتمل + مُختبر | `pipeline/stage_7_export.py` |
| **Post** | Embeddings | ⏳ لم يُبنَ بعد | `scripts/generate_embeddings.py` |

---

## حالة القوانين

| القانون | المرحلة | المواد | Chunks | ملاحظات |
|---------|---------|--------|--------|---------|
| **EG_PDPL** | ✅ Stage 7 (Released) | 56 | 61 | نظيف، صفر أخطاء |
| **EG_ESIGN** | ✅ Stage 7 (Released) | 30 | 30 | OCR بـ Gemini، صفر أخطاء |
| **EG_CIVIL_CODE** | ✅ Stage 7 (Released) | 1039 | 1041 | 111 مادة ملغاة مُسجَّلة |
| **EG_LABOR_2025** | ✅ Stage 7 (Released) | 308 | 321 | نظيف، صفر أخطاء (بعد إصلاح bugs عامة — انظر `DIAGNOSTIC_NOTE_EG_RENT_1969_EG_LABOR_2025.md`) |
| **EG_RENT_1969** | ⏸️ متوقف عند Stage 2.5 | 98 (مؤقت) | — | خطأ متبقٍّ واحد (تكرار المادة 35) — مشكلة جودة بيانات في مذكرة إيضاحية، وليس bug — بانتظار مراجعة بشرية، انظر ملاحظة التشخيص |

---

## مسارات البيانات

```
data/
├── raw_pdfs/           — ملفات PDF المصدر
├── raw_txts/           — ملفات TXT جاهزة (EG_PDPL فقط)
├── extracted_raw/      — Stage 1 مخرجات (.txt + _meta.json)
├── extracted_clean/    — Stage 1.3 مخرجات
├── cleanup_audit_logs/ — Stage 1.3 سجلات التدقيق
├── split_articles/     — Stage 2 مخرجات (articles.json لكل قانون)
├── enriched_articles/  — Stage 3 مخرجات (مع topic/keywords/summary)
├── chunks/             — Stage 3.7 مخرجات
├── human_review/       — Stage 4 مخرجات (JSON + CSV للمراجعة البشرية)
├── validated/          — Stage 5 مخرجات (validation_report.json)
├── assembled/          — Stage 6 مخرجات (articles_final + chunks_final)
└── releases/           — Stage 7 مخرجات (articles.json/.jsonl + chunks + metadata)
```

---

## أوامر التشغيل

```bash
# تشغيل القانون الكامل من البداية (Stages 1→7)
cd nezam-legal-corpus
python run_batch.py EG_PDPL

# تشغيل عدة قوانين
python run_batch.py EG_PDPL EG_ESIGN EG_CIVIL_CODE

# تشغيل القانون الافتراضي (BATCH_LAWS في run_batch.py)
python run_batch.py
```

---

## مفاتيح API المطلوبة

| المفتاح | المصدر | الحالة |
|---------|--------|--------|
| `GEMINI_API_KEYS` | Replit Secrets | ✅ مُعيَّن (متعدد) |
| `MONGODB_URI` | Replit Secrets (اختياري) | ⏳ غير مُعيَّن (Stage 7 يعمل بدونه) |

---

## إعدادات الأداء الحالية (settings.py)

| الإعداد | القيمة | الهدف |
|---------|--------|-------|
| `PRIMARY_MODEL` | `gemini-2.5-flash` (من env) | نموذج Stage 3 الإثراء |
| `ENRICH_BATCH_SIZE` | 150 مادة/طلب | استخدام ~97% من 250K TPM |
| `GEMINI_MAX_OUTPUT_TOKENS` | 65536 | أقصى output ممكن |

---

## Stage 5 — قواعد التحقق

| الكود | الاسم | الخطورة | التعريف |
|-------|-------|---------|---------|
| V001 | ENRICHMENT_INCOMPLETE | خطأ | حقل مطلوب (topic/summary/category) فارغ |
| V002 | INVALID_CATEGORY | تحذير | article_category ليس من القيم المسموحة |
| V003 | ENRICHMENT_ERROR | خطأ | فشل الإثراء في Stage 3 |
| V004 | REPEALED_MISMATCH | تحذير | is_repealed لا يتطابق مع law_registry |
| V005 | EMPTY_KEYWORDS | تحذير | قائمة keywords فارغة |

---

## Stage 6 — ما يحدث في Assembly

1. **إزالة التكرارات** بناءً على article_id (الأحدث يُحتفظ به)
2. **تطبيق is_repealed** من law_registry (المصدر الموثوق)
3. **تعيين is_current_version = True** لجميع المقالات
4. **ترتيب المقالات**: مواد رئيسية بالترتيب الرقمي، ثم مواد الإصدار
5. **نشر is_repealed للـ chunks** المرتبطة بالمواد الملغاة

---

## Stage 7 — ملفات الإصدار

لكل قانون في `data/releases/{LAW_ID}/`:
- `articles.json` — JSON مُنسَّق للمراجعة
- `articles.jsonl` — سطر JSON لكل مادة (للاستيراد في قواعد البيانات)
- `chunks.json` — JSON مُنسَّق
- `chunks.jsonl` — سطر JSON لكل chunk
- `release_metadata.json` — إحصاءات + إصدار Schema

---

## الخطوة التالية: Embeddings

```bash
# لم يُبنَ بعد: scripts/generate_embeddings.py
# النموذج: text-embedding-004
# المدخل: data/releases/{LAW_ID}/chunks.jsonl
# المخرج: embeddings مُخزَّنة في قاعدة بيانات vectors
```

**ملاحظة:** embeddings لا تحتاج لـ Gemini generative API — تستخدم Embedding API منفصل.

---

## القانون التالي المقترح

بناءً على خطة التنفيذ في `docs/03_ROADMAP.md`:

| الأولوية | القانون | المواد | نوع PDF | السبب |
|---------|---------|--------|---------|-------|
| 1 | **EG_EVIDENCE** | 99 | Mixed | قانون متوسط الحجم — لبناء الـ golden benchmark |
| 2 | **EG_LABOR** | 254 | Mixed | أول قانون كبير — اختبار الأداء |
| 3 | **EG_RENT** | 80 | Scanned | أول PDF ممسوح — اختبار OCR بالتكلفة الحقيقية |
