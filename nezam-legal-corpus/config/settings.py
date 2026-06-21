import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "gemini-2.0-flash")
EMBEDDING_MODEL = "text-embedding-004"
PROMPT_VERSION = "1.0.0"

CONFIDENCE_THRESHOLD = 0.85
PYMUPDF_MIN_CHARS = 200

DATA_DIR = BASE_DIR / "data"
RAW_PDFS_DIR = DATA_DIR / "raw_pdfs"
EXTRACTED_RAW_DIR = DATA_DIR / "extracted_raw"
EXTRACTED_CLEAN_DIR = DATA_DIR / "extracted_clean"
CLEANUP_AUDIT_DIR = DATA_DIR / "cleanup_audit_logs"


def _load_api_keys() -> list[str]:
    raw = os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.getenv("GEMINI_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


GEMINI_API_KEYS: list[str] = _load_api_keys()

GEMINI_FLASH_INPUT_COST_PER_1M = 0.10
GEMINI_FLASH_OUTPUT_COST_PER_1M = 0.40
GEMINI_FLASH_IMAGE_COST_PER_PAGE = 0.00258

GEMINI_MAX_RETRIES = 20
GEMINI_RETRY_BASE_DELAY = 2.0

OCR_PROMPT = """أنت محرك استخراج نصوص قانونية دقيق. مهمتك استخراج النص العربي الكامل من هذه الوثيقة القانونية المصرية بدقة تامة.

التعليمات:
- استخرج النص كما يظهر في الوثيقة دون أي تعديل أو ترجمة أو تلخيص.
- حافظ على ترقيم المواد والفصول والأبواب بالشكل الأصلي.
- احتفظ بفقرات النص كاملة مع علامات الترقيم الأصلية.
- احتفظ بمسافات الأسطر بين المواد (\n\n بين كل مادة والتالية).
- لا تضف أي تعليقات أو عناوين من عندك.
- إذا كان النص غير واضح في موضع ما، اكتب [غير واضح] في المكان المقابل.

ابدأ الاستخراج الآن:"""
