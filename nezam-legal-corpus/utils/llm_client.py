"""
Gemini LLM client with integrated key rotation.

On 429 / RESOURCE_EXHAUSTED:
  1. Mark current key rate-limited (60-min cooldown)
  2. Rotate to next available key
  3. Retry immediately with new key

On 500 / 503 / 504 (transient server errors):
  Exponential backoff on the same key.

If all keys are exhausted: block and wait — never crash.

KEY-PINNING RULE for ocr_pdf:
  Gemini File API files are scoped to the uploading API key's project.
  A file uploaded with key A cannot be accessed by key B.
  Therefore ocr_pdf pins one key for the entire upload+generate cycle.
  If the pinned key receives a 429 during generation, the whole operation
  restarts: mark the key rate-limited, get a new key, re-upload, retry.

GenerateContentConfig parameter guide (google-genai SDK):
──────────────────────────────────────────────────────────
temperature      float  0.0–2.0   0.0 for extraction/classification, 0.1 for creative boundaries
                                  Do NOT touch for gemini-3.x (uses its default of 1.0 internally)
top_p            float  0.0–1.0   Nucleus sampling; default ~0.95. Usually leave as model default.
top_k            int    1–100+    Hard token candidate limit; default ~40. Usually leave unset.
seed             int    any       Reproducibility seed. None = random. Useful for debugging.
max_output_tokens int   1–65536  Set to 65536 (model max) to give full breathing room.
thinking_budget  int    0–N       gemini-2.5-x: 0=off, N=token budget for reasoning.
thinking_level   str    enum      gemini-3.x: "OFF"|"LOW"|"MEDIUM"|"HIGH"
response_schema  dict   OpenAPI   Guarantees valid JSON matching schema — no regex parsing needed.
system_instruction str  any       Role/persona definition, separate from user prompt.

Long-context best practice (Google docs):
─────────────────────────────────────────
Place the task description BEFORE the document AND repeat it AFTER ("question sandwich").
Model attention is strongest at the beginning and end of long contexts.
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
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_RETRY_BASE_DELAY,
    GEMINI_FLASH_INPUT_COST_PER_1M,
    GEMINI_FLASH_OUTPUT_COST_PER_1M,
    OCR_SYSTEM_INSTRUCTION,
)
from utils.cost_tracker import CostTracker
from utils import key_manager as _km

logger = logging.getLogger(__name__)

_RATE_LIMIT_CODES  = {429}
_TRANSIENT_CODES   = {500, 503, 504}
_INVALID_KEY_CODES = {403}


class QuotaExhaustedError(Exception):
    """
    Raised by generate_text when fast_fail_on_quota=True and ALL API keys have
    exhausted their daily quota (RPD) for the requested model.

    The caller should switch to a different model or wait until keys reset.
    """
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        super().__init__(
            f"All API keys have exhausted their daily quota for model '{model_name}'. "
            "Switch to a fallback model or wait until UTC midnight for quota reset."
        )


# ── Error classifiers ─────────────────────────────────────────────────────────

def _is_rate_limit(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return exc.code in _RATE_LIMIT_CODES
    msg = str(exc).upper()
    return "RESOURCE_EXHAUSTED" in msg or "429" in msg


def _is_daily_quota(exc: Exception) -> bool:
    msg = str(exc).upper()
    rpm_kw = ("PER MINUTE", "PER-MINUTE", "MINUTE", "RPM", "REQUESTS_PER_MINUTE")
    if any(k in msg for k in rpm_kw):
        return False
    rpd_kw = ("QUOTA", "BILLING", "PLAN", "PER DAY", "DAILY", "RPD")
    return any(k in msg for k in rpd_kw)


def _is_invalid_key(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return exc.code in _INVALID_KEY_CODES
    msg = str(exc).upper()
    return "403" in msg or "PERMISSION_DENIED" in msg or "API_KEY_INVALID" in msg


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return exc.code in _TRANSIENT_CODES
    return False


def _make_client(key: str) -> genai.Client:
    return genai.Client(api_key=key)


# ── Config builder ────────────────────────────────────────────────────────────

def _build_config(
    temperature: float,
    max_output_tokens: int,
    top_p: float | None,
    top_k: int | None,
    seed: int | None,
    thinking_budget: int | None,
    thinking_level: str | None,
    response_schema: dict | None,
    system_instruction: str | None,
) -> types.GenerateContentConfig:
    """
    Build a GenerateContentConfig from keyword arguments, only setting
    parameters that are explicitly provided (avoids overriding model defaults
    with None values).
    """
    kwargs: dict = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }

    # Sampling parameters (leave as model default unless explicitly set)
    if top_p is not None:
        kwargs["top_p"] = top_p
    if top_k is not None:
        kwargs["top_k"] = top_k
    if seed is not None:
        kwargs["seed"] = seed

    # Thinking configuration
    # Only one of thinking_budget / thinking_level should be set.
    # thinking_budget → gemini-2.5-x models
    # thinking_level  → gemini-3.x models (including gemini-3.5-flash)
    if thinking_budget is not None:
        kwargs["thinking_config"] = {"thinking_budget": thinking_budget}
    elif thinking_level is not None:
        kwargs["thinking_config"] = {"thinking_level": thinking_level}

    # Structured output (guarantees valid JSON — no regex parsing needed)
    if response_schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = response_schema

    if system_instruction is not None:
        kwargs["system_instruction"] = system_instruction

    return types.GenerateContentConfig(**kwargs)


# ── generate_text ─────────────────────────────────────────────────────────────

def generate_text(
    prompt: str,
    cost_tracker: CostTracker,
    stage: str,
    law_id: str,
    model_name: str = PRIMARY_MODEL,
    # ── Core generation parameters ──────────────────────────────────────────
    temperature: float = 0.0,
    max_output_tokens: int = GEMINI_MAX_OUTPUT_TOKENS,  # 65536 = model maximum
    # ── Sampling (leave None to use model defaults) ──────────────────────────
    top_p: float | None = None,    # default ~0.95 — nucleus sampling threshold
    top_k: int | None = None,      # default ~40  — hard token candidate limit
    seed: int | None = None,       # set for reproducible outputs (debugging)
    # ── Thinking (use the param matching your model) ─────────────────────────
    thinking_budget: int | None = None,   # gemini-2.5-x: 0=off, N=token budget
    thinking_level: str | None = None,    # gemini-3.x:  "OFF"|"LOW"|"MEDIUM"|"HIGH"
    # ── Output format ────────────────────────────────────────────────────────
    response_schema: dict | None = None,         # guarantees JSON matching schema
    system_instruction: str | None = None,        # role/persona (separate from prompt)
    # ── Quota handling ───────────────────────────────────────────────────────
    fast_fail_on_quota: bool = False,
    # When True: if ALL keys exhaust their daily RPD limit for this model,
    # raises QuotaExhaustedError immediately instead of blocking.
    # Use this when you have a fallback model to switch to.
    # When False (default): blocks and waits for key cooldowns (original behavior).
) -> str:
    """
    Generate text from Gemini with full parameter control, key rotation,
    and exponential backoff on transient errors.

    Parameters
    ----------
    max_output_tokens : int
        Defaults to 65536 (model maximum) — gives the model full breathing room.
        Only lower this for cost optimisation on stages that rarely need long output.
    thinking_budget : int | None
        For gemini-2.5-x models. 0 = disabled, N = token budget.
    thinking_level : str | None
        For gemini-3.x models. "OFF" | "LOW" | "MEDIUM" | "HIGH".
        Do NOT set both thinking_budget and thinking_level in the same call.
    response_schema : dict | None
        JSON Schema (OpenAPI 3.0 subset). When set, the API guarantees valid JSON
        matching the schema — no manual parsing or regex extraction needed.
    system_instruction : str | None
        Role/persona definition, kept separate from the user prompt per
        Google's prompting best practices.
    """
    manager = _km.get_manager()
    config = _build_config(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        top_p=top_p,
        top_k=top_k,
        seed=seed,
        thinking_budget=thinking_budget,
        thinking_level=thinking_level,
        response_schema=response_schema,
        system_instruction=system_instruction,
    )
    transient_backoff = GEMINI_RETRY_BASE_DELAY

    def _get_key() -> str:
        """Get an available key. When fast_fail_on_quota is set, raise immediately
        instead of blocking if all keys are exhausted for this model."""
        if fast_fail_on_quota and manager.all_keys_exhausted():
            raise QuotaExhaustedError(model_name)
        return manager.get_available_key_or_wait()

    for attempt in range(GEMINI_MAX_RETRIES):
        current_key = _get_key()
        key_suffix  = f"****{current_key[-3:]}"
        client      = _make_client(current_key)

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

        except QuotaExhaustedError:
            raise   # always propagate — caller handles model switching

        except Exception as exc:
            if _is_rate_limit(exc):
                if _is_daily_quota(exc):
                    logger.warning("[%s] Key %s daily quota exhausted — rotating.", stage, key_suffix)
                    manager.mark_daily_quota_exhausted(current_key)
                    cost_tracker.record_key_failure(key_suffix)
                    # Fast-fail path: if all keys are now exhausted, raise immediately
                    # so the caller can switch to a fallback model without blocking.
                    if fast_fail_on_quota and manager.all_keys_exhausted():
                        raise QuotaExhaustedError(model_name)
                    new_key = manager.get_available_key_or_wait()
                else:
                    logger.warning("[%s] Key %s hit RPM limit — rotating.", stage, key_suffix)
                    manager.mark_rpm_limited(current_key)
                    cost_tracker.record_key_failure(key_suffix)
                    new_key = manager.get_available_key_or_wait()
                new_suffix = f"****{new_key[-3:]}"
                cost_tracker.record_rotation(key_suffix, new_suffix, "RATE_LIMIT")
                transient_backoff = GEMINI_RETRY_BASE_DELAY
                continue

            if _is_invalid_key(exc):
                logger.warning("[%s] Key %s is invalid/leaked — disabling permanently.", stage, key_suffix)
                manager.mark_permanently_disabled(current_key, reason=str(exc)[:120])
                cost_tracker.record_key_failure(key_suffix)
                new_key    = manager.get_available_key_or_wait()
                new_suffix = f"****{new_key[-3:]}"
                cost_tracker.record_rotation(key_suffix, new_suffix, "INVALID_KEY")
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


# ── PDF upload helper ─────────────────────────────────────────────────────────

def _upload_pdf_with_key(client: genai.Client, pdf_path: Path) -> object:
    """Upload PDF and wait for ACTIVE state. Returns the uploaded file object."""
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
    return uploaded


# ── ocr_pdf ───────────────────────────────────────────────────────────────────

def ocr_pdf(
    pdf_path: Path,
    prompt: str,
    cost_tracker: CostTracker,
    stage: str,
    law_id: str,
    model_name: str = PRIMARY_MODEL,
) -> str:
    """
    Upload a PDF to Gemini and extract its text using the given prompt.

    Design decisions:
    - Key-pinning: same API key used for upload AND generation (File API scope).
    - Thinking: NOT enabled (OCR is pure extraction; thinking adds cost/latency
      without improving verbatim text accuracy).
    - max_output_tokens: 65536 (model maximum) — legal PDFs can be very long.
    - system_instruction: OCR_SYSTEM_INSTRUCTION (verbatim extraction role).

    If generation hits 429, the key is marked rate-limited and the entire
    operation restarts with a new key (re-upload included).
    """
    manager = _km.get_manager()

    # OCR config: full output, no thinking (extraction ≠ reasoning)
    ocr_config = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=65536,
        system_instruction=OCR_SYSTEM_INSTRUCTION,
        # thinking intentionally NOT set — model uses no/minimal internal reasoning
        # for pure verbatim extraction tasks
    )

    for outer_attempt in range(GEMINI_MAX_RETRIES):
        pinned_key = manager.get_available_key_or_wait()
        key_suffix = f"****{pinned_key[-3:]}"
        client     = _make_client(pinned_key)

        try:
            uploaded = _upload_pdf_with_key(client, pdf_path)
        except Exception as exc:
            if _is_rate_limit(exc):
                logger.warning("[%s] Key %s hit rate limit during upload — rotating.", stage, key_suffix)
                manager.mark_rate_limited(pinned_key, reason="RESOURCE_EXHAUSTED on upload")
                cost_tracker.record_key_failure(key_suffix)
                continue
            if _is_invalid_key(exc):
                logger.warning("[%s] Key %s is invalid during upload — disabling permanently.", stage, key_suffix)
                manager.mark_permanently_disabled(pinned_key, reason=str(exc)[:120])
                cost_tracker.record_key_failure(key_suffix)
                continue
            raise

        transient_backoff = GEMINI_RETRY_BASE_DELAY
        for gen_attempt in range(GEMINI_MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[uploaded, prompt],
                    config=ocr_config,
                )
                usage = response.usage_metadata
                manager.record_request(pinned_key, success=True)

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
                    if _is_daily_quota(exc):
                        logger.warning("[%s] Key %s daily quota exhausted during OCR — rotating.", stage, key_suffix)
                        manager.mark_daily_quota_exhausted(pinned_key)
                        cost_tracker.record_key_failure(key_suffix)
                        new_key    = manager.get_available_key_or_wait()
                        new_suffix = f"****{new_key[-3:]}"
                        cost_tracker.record_rotation(key_suffix, new_suffix, "RPD_EXHAUSTED")
                        try:
                            client.files.delete(name=uploaded.name)
                        except Exception:
                            pass
                        break

                    if gen_attempt < 2:
                        wait_secs = 30 * (gen_attempt + 1)
                        logger.warning("[%s] Key %s RPM limit (attempt %d) — waiting %ds.", stage, key_suffix, gen_attempt + 1, wait_secs)
                        time.sleep(wait_secs)
                        continue

                    logger.warning("[%s] Key %s exhausted RPM retries — rotating.", stage, key_suffix)
                    manager.mark_rpm_limited(pinned_key)
                    cost_tracker.record_key_failure(key_suffix)
                    new_key    = manager.get_available_key_or_wait()
                    new_suffix = f"****{new_key[-3:]}"
                    cost_tracker.record_rotation(key_suffix, new_suffix, "RPM_EXHAUSTED")
                    try:
                        client.files.delete(name=uploaded.name)
                    except Exception:
                        pass
                    break

                if _is_invalid_key(exc):
                    logger.warning("[%s] Key %s invalid during OCR — disabling.", stage, key_suffix)
                    manager.mark_permanently_disabled(pinned_key, reason=str(exc)[:120])
                    cost_tracker.record_key_failure(key_suffix)
                    new_key    = manager.get_available_key_or_wait()
                    new_suffix = f"****{new_key[-3:]}"
                    cost_tracker.record_rotation(key_suffix, new_suffix, "INVALID_KEY")
                    try:
                        client.files.delete(name=uploaded.name)
                    except Exception:
                        pass
                    break

                if _is_transient(exc):
                    manager.record_request(pinned_key, success=False)
                    cost_tracker.record_key_failure(key_suffix)
                    if gen_attempt < GEMINI_MAX_RETRIES - 1:
                        logger.warning("[%s] Transient OCR error (attempt %d): %s. Retrying in %.1fs.", stage, gen_attempt + 1, exc, transient_backoff)
                        time.sleep(transient_backoff)
                        transient_backoff = min(transient_backoff * 2, 60.0)
                        continue

                manager.record_request(pinned_key, success=False)
                cost_tracker.record_key_failure(key_suffix)
                raise

    raise RuntimeError(f"ocr_pdf: failed after {GEMINI_MAX_RETRIES} outer attempts")
