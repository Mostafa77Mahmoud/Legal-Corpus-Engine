"""
Gemini API Key Rotation Manager

Thread-safe pool of API keys with per-key cooldown tracking.
When a key receives a 429 / RESOURCE_EXHAUSTED response it is
marked unavailable for COOLDOWN_SECONDS and the pool rotates to
the next available key automatically.

If all keys are exhausted the manager blocks and polls until the
first key's cooldown expires, then resumes — the pipeline never
crashes due to quota exhaustion.

Singleton access via get_manager().
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = int(os.environ.get("KEY_COOLDOWN_SECONDS", str(60 * 60)))  # default 60 min
_POLL_INTERVAL = 30          # seconds between exhaustion-wait checks

_LOG_DIR = Path(__file__).parent.parent / "logs"
_ROTATION_LOG = _LOG_DIR / "key_rotation.log"


def _rotation_logger() -> logging.Logger:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    rot = logging.getLogger("key_rotation")
    if not rot.handlers:
        fh = logging.FileHandler(_ROTATION_LOG, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        rot.addHandler(fh)
        rot.setLevel(logging.INFO)
        rot.propagate = False
    return rot


@dataclass
class _KeyRecord:
    key: str
    _rate_limited: bool = field(default=False, repr=False)
    _cooldown_end: float | None = field(default=None, repr=False)
    total_requests: int = 0
    total_failures: int = 0

    @property
    def suffix(self) -> str:
        return f"****{self.key[-3:]}" if len(self.key) >= 3 else f"****{self.key}"

    def is_available(self) -> bool:
        if not self._rate_limited:
            return True
        # _cooldown_end=None with _rate_limited=True → permanently disabled
        if self._cooldown_end is None:
            return False
        now = time.time()
        if now >= self._cooldown_end:
            self._rate_limited = False
            self._cooldown_end = None
            logger.info("Key %s cooldown expired — returned to pool.", self.suffix)
            return True
        return False

    def cooldown_remaining_seconds(self) -> float:
        if self._cooldown_end is None:
            return 0.0
        return max(0.0, self._cooldown_end - time.time())

    def apply_cooldown(self) -> None:
        self._rate_limited = True
        self._cooldown_end = time.time() + COOLDOWN_SECONDS
        self.total_failures += 1


class KeyManager:
    """Thread-safe Gemini API key pool with automatic rotation and cooldown."""

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("KeyManager requires at least one API key.")
        self._records: list[_KeyRecord] = [_KeyRecord(key=k) for k in keys]
        self._index: int = 0
        self._lock = threading.Lock()
        self._rotation_log = _rotation_logger()
        self._total_rotations: int = 0
        logger.info("KeyManager initialised with %d key(s).", len(self._records))

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def get_current_key(self) -> str:
        with self._lock:
            return self._records[self._index].key

    def mark_rate_limited(self, key: str, reason: str = "RESOURCE_EXHAUSTED") -> None:
        with self._lock:
            record = self._record_for(key)
            if record is None or record._rate_limited:
                return
            record.apply_cooldown()
            old_suffix = record.suffix
            self._rotation_log.info(
                "RATE_LIMITED  %s  reason=%s  cooldown=%ds",
                old_suffix, reason, COOLDOWN_SECONDS,
            )
            logger.warning(
                "Key %s rate-limited (%s). Cooldown for %d min.",
                old_suffix, reason, COOLDOWN_SECONDS // 60,
            )

    def mark_rpm_limited(self, key: str, cooldown_seconds: int = 90) -> None:
        """Short cooldown for per-minute quota hits (RPM limit, not daily quota)."""
        with self._lock:
            record = self._record_for(key)
            if record is None or record._rate_limited:
                return
            record._rate_limited = True
            record._cooldown_end = time.time() + cooldown_seconds
            record.total_failures += 1
            self._rotation_log.info(
                "RPM_LIMITED  %s  cooldown=%ds", record.suffix, cooldown_seconds,
            )
            logger.warning(
                "Key %s RPM-limited. Cooldown for %ds.",
                record.suffix, cooldown_seconds,
            )

    def mark_permanently_disabled(self, key: str, reason: str = "INVALID_KEY") -> None:
        """Permanently remove a key from the pool (leaked / revoked keys)."""
        with self._lock:
            record = self._record_for(key)
            if record is None:
                return
            record._rate_limited = True
            record._cooldown_end = None   # None + _rate_limited=True → never recovers
            record.total_failures += 1
            self._rotation_log.info(
                "DISABLED  %s  reason=%s",
                record.suffix, reason,
            )
            logger.warning(
                "Key %s permanently disabled: %s.",
                record.suffix, reason,
            )

    def rotate_key(self) -> str | None:
        """Advance to the next available key. Returns the new key or None if all exhausted."""
        with self._lock:
            old_record = self._records[self._index]
            n = len(self._records)
            for step in range(1, n + 1):
                next_index = (self._index + step) % n
                if self._records[next_index].is_available():
                    self._index = next_index
                    new_record = self._records[self._index]
                    self._total_rotations += 1
                    self._rotation_log.info(
                        "ROTATED  %s -> %s  reason=quota_exhausted_on_previous",
                        old_record.suffix, new_record.suffix,
                    )
                    logger.info(
                        "Rotated API key: %s → %s",
                        old_record.suffix, new_record.suffix,
                    )
                    return new_record.key
            return None

    def get_available_key(self) -> str | None:
        """Return the current key if available, otherwise try to rotate. Non-blocking."""
        with self._lock:
            current = self._records[self._index]
            if current.is_available():
                return current.key
        return self.rotate_key()

    def get_available_key_or_wait(self) -> str:
        """Block until a key becomes available, then return it."""
        while True:
            key = self.get_available_key()
            if key is not None:
                return key
            self._warn_all_exhausted()
            time.sleep(_POLL_INTERVAL)

    def all_keys_exhausted(self) -> bool:
        with self._lock:
            return not any(r.is_available() for r in self._records)

    def record_request(self, key: str, *, success: bool) -> None:
        with self._lock:
            record = self._record_for(key)
            if record is None:
                return
            record.total_requests += 1
            if not success:
                record.total_failures += 1

    # ------------------------------------------------------------------ #
    # Stats                                                                #
    # ------------------------------------------------------------------ #

    @property
    def total_rotations(self) -> int:
        with self._lock:
            return self._total_rotations

    def per_key_stats(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "suffix": r.suffix,
                    "total_requests": r.total_requests,
                    "total_failures": r.total_failures,
                    "is_rate_limited": r._rate_limited,
                    "cooldown_remaining_seconds": round(r.cooldown_remaining_seconds()),
                }
                for r in self._records
            ]

    # ------------------------------------------------------------------ #
    # Private                                                              #
    # ------------------------------------------------------------------ #

    def _record_for(self, key: str) -> "_KeyRecord | None":
        for r in self._records:
            if r.key == key:
                return r
        return None

    def _warn_all_exhausted(self) -> None:
        with self._lock:
            lines = ["All Gemini API keys are rate-limited. Waiting for cooldown…"]
            for r in self._records:
                remaining = r.cooldown_remaining_seconds()
                lines.append(
                    f"  {r.suffix}  →  available in {remaining/60:.1f} min"
                )
        logger.warning("\n".join(lines))


# ------------------------------------------------------------------ #
# Module-level singleton                                               #
# ------------------------------------------------------------------ #

_instance: KeyManager | None = None
_instance_lock = threading.Lock()


def get_manager() -> KeyManager:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                from config.settings import GEMINI_API_KEYS
                _instance = KeyManager(GEMINI_API_KEYS)
    return _instance


def reset_manager() -> None:
    """Reset the singleton — used in tests only."""
    global _instance
    with _instance_lock:
        _instance = None
