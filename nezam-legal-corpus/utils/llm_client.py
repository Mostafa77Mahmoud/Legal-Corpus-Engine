"""
Gemini LLM client with integrated key rotation.

On 429 / RESOURCE_EXHAUSTED:
  1. Mark current key rate-limited (60-min cooldown)
  2. Rotate to next available key
  3. Retry immediately with new key

On 500 / 503 / 504 (transient server errors):
  Exponential backoff on the same key.

If all keys are exhausted: block and wait — never crash.
"""

import logging
import time
from pathlib import Path

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from config.settings import (
    PRIMARY_MODEL,
    GEMINI_MAX_RETRIES,
    GEMINI_RETRY_BASE_DELAY,
    GEMINI_FLASH_INPUT_COST_PER_1M,
    GEMINI_FLASH_OUTPUT_COST_PER_1M,
)
from utils.cost_tracker import CostTracker
from utils import key_manager as _km

logger = logging.getLogger(__name__)

_RATE_LIMIT_CODES = {429}
_TRANSIENT_CODES = {500, 503, 504}


def _is_rate_limit(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return exc.code in _RATE_LIMIT_CODES
    msg = str(exc).upper()
    return "RESOURCE_EXHAUSTED" in msg or "429" in msg


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return exc.code in _TRANSIENT_CODES
    return False


def _make_client(key: str) -> genai.Client:
    return genai.Client(api_key=key)


def generate_text(
    prompt: str,
    cost_tracker: CostTracker,
    stage: str,
    law_id: str,
    model_name: str = PRIMARY_MODEL,
    temperature: float = 0.0,
    max_output_tokens: int = 8192,
) -> str:
    manager = _km.get_manager()
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )
    transient_backoff = GEMINI_RETRY_BASE_DELAY

    for attempt in range(GEMINI_MAX_RETRIES):
        current_key = manager.get_available_key_or_wait()
        key_suffix = f"****{current_key[-3:]}"
        client = _make_client(current_key)

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            usage = response.usage_metadata
            manager.record_request(current_key, success=True)
            cost_tracker.record(
                stage=stage,
                law_id=law_id,
                model=model_name,
                input_tokens=usage.prompt_token_count or 0,
                output_tokens=usage.candidates_token_count or 0,
                input_cost_per_1m=GEMINI_FLASH_INPUT_COST_PER_1M,
                output_cost_per_1m=GEMINI_FLASH_OUTPUT_COST_PER_1M,
                api_key_suffix=key_suffix,
            )
            return response.text

        except Exception as exc:
            if _is_rate_limit(exc):
                logger.warning("[%s] Key %s hit rate limit — rotating.", stage, key_suffix)
                manager.mark_rate_limited(current_key, reason="RESOURCE_EXHAUSTED")
                cost_tracker.record_key_failure(key_suffix)
                new_key = manager.get_available_key_or_wait()
                new_suffix = f"****{new_key[-3:]}"
                cost_tracker.record_rotation(key_suffix, new_suffix, "RESOURCE_EXHAUSTED")
                transient_backoff = GEMINI_RETRY_BASE_DELAY
                continue

            if _is_transient(exc):
                manager.record_request(current_key, success=False)
                cost_tracker.record_key_failure(key_suffix)
                if attempt < GEMINI_MAX_RETRIES - 1:
                    logger.warning(
                        "[%s] Transient error (attempt %d): %s. Retrying in %.1fs.",
                        stage, attempt + 1, exc, transient_backoff,
                    )
                    time.sleep(transient_backoff)
                    transient_backoff = min(transient_backoff * 2, 60.0)
                    continue

            manager.record_request(current_key, success=False)
            cost_tracker.record_key_failure(key_suffix)
            raise

    raise RuntimeError(f"generate_text: failed after {GEMINI_MAX_RETRIES} attempts")


def ocr_pdf(
    pdf_path: Path,
    prompt: str,
    cost_tracker: CostTracker,
    stage: str,
    law_id: str,
    model_name: str = PRIMARY_MODEL,
) -> str:
    manager = _km.get_manager()
    transient_backoff = GEMINI_RETRY_BASE_DELAY

    current_key = manager.get_available_key_or_wait()
    key_suffix = f"****{current_key[-3:]}"
    client = _make_client(current_key)

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
        current_key = manager.get_available_key_or_wait()
        key_suffix = f"****{current_key[-3:]}"
        client = _make_client(current_key)

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
            manager.record_request(current_key, success=True)

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
                api_key_suffix=key_suffix,
            )
            return response.text

        except Exception as exc:
            if _is_rate_limit(exc):
                logger.warning("[%s] Key %s hit rate limit during OCR — rotating.", stage, key_suffix)
                manager.mark_rate_limited(current_key, reason="RESOURCE_EXHAUSTED")
                cost_tracker.record_key_failure(key_suffix)
                new_key = manager.get_available_key_or_wait()
                new_suffix = f"****{new_key[-3:]}"
                cost_tracker.record_rotation(key_suffix, new_suffix, "RESOURCE_EXHAUSTED")
                transient_backoff = GEMINI_RETRY_BASE_DELAY
                continue

            if _is_transient(exc):
                manager.record_request(current_key, success=False)
                cost_tracker.record_key_failure(key_suffix)
                if attempt < GEMINI_MAX_RETRIES - 1:
                    logger.warning(
                        "[%s] Transient OCR error (attempt %d): %s. Retrying in %.1fs.",
                        stage, attempt + 1, exc, transient_backoff,
                    )
                    time.sleep(transient_backoff)
                    transient_backoff = min(transient_backoff * 2, 60.0)
                    continue

            manager.record_request(current_key, success=False)
            cost_tracker.record_key_failure(key_suffix)
            raise

    raise RuntimeError(f"ocr_pdf: failed after {GEMINI_MAX_RETRIES} attempts")
