import time
import logging
from pathlib import Path

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from config.settings import (
    GEMINI_API_KEY,
    PRIMARY_MODEL,
    GEMINI_MAX_RETRIES,
    GEMINI_RETRY_BASE_DELAY,
    GEMINI_FLASH_INPUT_COST_PER_1M,
    GEMINI_FLASH_OUTPUT_COST_PER_1M,
)
from utils.cost_tracker import CostTracker

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 503, 504}


def _client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. Add it to your .env file or environment."
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return exc.code in _RETRYABLE_STATUS_CODES
    return False


def generate_text(
    prompt: str,
    cost_tracker: CostTracker,
    stage: str,
    law_id: str,
    model_name: str = PRIMARY_MODEL,
    temperature: float = 0.0,
    max_output_tokens: int = 8192,
) -> str:
    client = _client()
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )

    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            usage = response.usage_metadata
            cost_tracker.record(
                stage=stage,
                law_id=law_id,
                model=model_name,
                input_tokens=usage.prompt_token_count or 0,
                output_tokens=usage.candidates_token_count or 0,
                input_cost_per_1m=GEMINI_FLASH_INPUT_COST_PER_1M,
                output_cost_per_1m=GEMINI_FLASH_OUTPUT_COST_PER_1M,
            )
            return response.text
        except Exception as exc:
            if _is_retryable(exc) and attempt < GEMINI_MAX_RETRIES - 1:
                delay = GEMINI_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Gemini transient error (attempt %d): %s. Retrying in %.1fs.",
                    attempt + 1, exc, delay,
                )
                time.sleep(delay)
            else:
                raise

    raise RuntimeError("generate_text: exhausted all retries")


def ocr_pdf(
    pdf_path: Path,
    prompt: str,
    cost_tracker: CostTracker,
    stage: str,
    law_id: str,
    model_name: str = PRIMARY_MODEL,
) -> str:
    client = _client()

    logger.info("Uploading PDF to Gemini File API: %s", pdf_path.name)
    uploaded = client.files.upload(
        file=str(pdf_path),
        config={"mime_type": "application/pdf"},
    )

    wait_seconds = 0
    while uploaded.state and uploaded.state.name == "PROCESSING":
        if wait_seconds > 120:
            raise TimeoutError(
                f"Gemini file processing timed out after {wait_seconds}s for {pdf_path.name}"
            )
        time.sleep(5)
        wait_seconds += 5
        uploaded = client.files.get(name=uploaded.name)

    if uploaded.state and uploaded.state.name == "FAILED":
        raise RuntimeError(
            f"Gemini file processing failed for {pdf_path.name}: {uploaded.state}"
        )

    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[uploaded, prompt],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=65536,
                ),
            )
            usage = response.usage_metadata

            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass

            cost_tracker.record(
                stage=stage,
                law_id=law_id,
                model=model_name,
                input_tokens=usage.prompt_token_count or 0,
                output_tokens=usage.candidates_token_count or 0,
                input_cost_per_1m=GEMINI_FLASH_INPUT_COST_PER_1M,
                output_cost_per_1m=GEMINI_FLASH_OUTPUT_COST_PER_1M,
            )
            return response.text

        except Exception as exc:
            if _is_retryable(exc) and attempt < GEMINI_MAX_RETRIES - 1:
                delay = GEMINI_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Gemini OCR retry %d for %s: %s. Waiting %.1fs.",
                    attempt + 1, pdf_path.name, exc, delay,
                )
                time.sleep(delay)
            else:
                raise

    raise RuntimeError("ocr_pdf: exhausted all retries")
