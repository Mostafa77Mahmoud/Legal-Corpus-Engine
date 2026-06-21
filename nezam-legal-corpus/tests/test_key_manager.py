"""
Unit tests for utils/key_manager.py

Covers:
  - Basic rotation
  - Cooldown enforcement
  - Cooldown expiration / automatic recovery
  - All-keys-exhausted detection
  - Thread-safety smoke test
  - Cost tracker rotation recording
  - Settings key loading
"""

import sys
import time
import threading
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest

from utils.key_manager import KeyManager, COOLDOWN_SECONDS, reset_manager
from utils.cost_tracker import CostTracker


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def make_manager(*keys: str) -> KeyManager:
    return KeyManager(list(keys))


# ------------------------------------------------------------------ #
# Construction                                                          #
# ------------------------------------------------------------------ #

def test_requires_at_least_one_key():
    with pytest.raises(ValueError):
        KeyManager([])


def test_single_key_returns_key():
    mgr = make_manager("KEY_AAA")
    assert mgr.get_current_key() == "KEY_AAA"


def test_multiple_keys_starts_on_first():
    mgr = make_manager("KEY_AAA", "KEY_BBB", "KEY_CCC")
    assert mgr.get_current_key() == "KEY_AAA"


# ------------------------------------------------------------------ #
# Suffix helper                                                         #
# ------------------------------------------------------------------ #

def test_suffix_last_three_chars():
    mgr = make_manager("ABCDEFXYZ")
    assert mgr.per_key_stats()[0]["suffix"] == "****XYZ"


def test_suffix_short_key():
    mgr = make_manager("AB")
    assert mgr.per_key_stats()[0]["suffix"] == "****AB"


# ------------------------------------------------------------------ #
# Rotation                                                              #
# ------------------------------------------------------------------ #

def test_rotate_advances_to_next_key():
    mgr = make_manager("KEY_AAA", "KEY_BBB", "KEY_CCC")
    new_key = mgr.rotate_key()
    assert new_key == "KEY_BBB"
    assert mgr.get_current_key() == "KEY_BBB"


def test_rotate_wraps_around():
    mgr = make_manager("KEY_AAA", "KEY_BBB")
    mgr.rotate_key()               # → BBB
    new_key = mgr.rotate_key()     # → AAA (wraps)
    assert new_key == "KEY_AAA"


def test_rotate_increments_total_rotations():
    mgr = make_manager("KEY_AAA", "KEY_BBB", "KEY_CCC")
    assert mgr.total_rotations == 0
    mgr.rotate_key()
    assert mgr.total_rotations == 1
    mgr.rotate_key()
    assert mgr.total_rotations == 2


def test_get_available_key_returns_current_when_available():
    mgr = make_manager("KEY_AAA", "KEY_BBB")
    assert mgr.get_available_key() == "KEY_AAA"


# ------------------------------------------------------------------ #
# Cooldown enforcement                                                  #
# ------------------------------------------------------------------ #

def test_mark_rate_limited_makes_key_unavailable():
    mgr = make_manager("KEY_AAA", "KEY_BBB")
    mgr.mark_rate_limited("KEY_AAA")
    # current key is rate-limited; get_available_key should rotate to BBB
    key = mgr.get_available_key()
    assert key == "KEY_BBB"


def test_rate_limited_key_not_returned_by_get_available():
    mgr = make_manager("KEY_AAA")
    mgr.mark_rate_limited("KEY_AAA")
    assert mgr.all_keys_exhausted() is True
    key = mgr.get_available_key()
    assert key is None


def test_marking_already_limited_key_is_idempotent():
    mgr = make_manager("KEY_AAA", "KEY_BBB")
    mgr.mark_rate_limited("KEY_AAA")
    cooldown_end_before = mgr._records[0]._cooldown_end
    time.sleep(0.05)
    mgr.mark_rate_limited("KEY_AAA")   # second call should be ignored
    assert mgr._records[0]._cooldown_end == cooldown_end_before


def test_all_keys_exhausted_returns_true_when_all_limited():
    mgr = make_manager("KEY_AAA", "KEY_BBB")
    mgr.mark_rate_limited("KEY_AAA")
    mgr.mark_rate_limited("KEY_BBB")
    assert mgr.all_keys_exhausted() is True


def test_all_keys_exhausted_returns_false_with_one_available():
    mgr = make_manager("KEY_AAA", "KEY_BBB")
    mgr.mark_rate_limited("KEY_AAA")
    assert mgr.all_keys_exhausted() is False


# ------------------------------------------------------------------ #
# Cooldown expiration / automatic recovery                              #
# ------------------------------------------------------------------ #

def test_key_recovers_after_cooldown_expires():
    mgr = make_manager("KEY_AAA")
    mgr.mark_rate_limited("KEY_AAA")

    # Manually fast-forward the cooldown_end to the past
    mgr._records[0]._cooldown_end = time.time() - 1.0

    assert mgr.all_keys_exhausted() is False
    key = mgr.get_available_key()
    assert key == "KEY_AAA"


def test_key_is_unavailable_before_cooldown_expires():
    mgr = make_manager("KEY_AAA")
    mgr.mark_rate_limited("KEY_AAA")

    # Cooldown is still in the future
    remaining = mgr._records[0].cooldown_remaining_seconds()
    assert remaining > 0


def test_rotate_skips_rate_limited_key_and_returns_available():
    mgr = make_manager("KEY_AAA", "KEY_BBB", "KEY_CCC")
    mgr.mark_rate_limited("KEY_AAA")
    mgr.mark_rate_limited("KEY_BBB")
    key = mgr.rotate_key()
    assert key == "KEY_CCC"


def test_rotate_returns_none_when_all_exhausted():
    mgr = make_manager("KEY_AAA", "KEY_BBB")
    mgr.mark_rate_limited("KEY_AAA")
    mgr.mark_rate_limited("KEY_BBB")
    key = mgr.rotate_key()
    assert key is None


# ------------------------------------------------------------------ #
# per_key_stats                                                         #
# ------------------------------------------------------------------ #

def test_record_request_increments_stats():
    mgr = make_manager("KEY_AAA")
    mgr.record_request("KEY_AAA", success=True)
    mgr.record_request("KEY_AAA", success=True)
    mgr.record_request("KEY_AAA", success=False)
    stats = mgr.per_key_stats()
    assert stats[0]["total_requests"] == 3
    assert stats[0]["total_failures"] == 1


def test_mark_rate_limited_increments_failures():
    mgr = make_manager("KEY_AAA", "KEY_BBB")
    mgr.mark_rate_limited("KEY_AAA")
    stats = {s["suffix"]: s for s in mgr.per_key_stats()}
    assert stats["****AAA"]["total_failures"] == 1


# ------------------------------------------------------------------ #
# Thread safety smoke test                                              #
# ------------------------------------------------------------------ #

def test_concurrent_rotations_are_safe():
    mgr = make_manager("KEY_AAA", "KEY_BBB", "KEY_CCC", "KEY_DDD")
    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(50):
                mgr.get_available_key()
                mgr.record_request(mgr.get_current_key(), success=True)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread-safety errors: {errors}"


# ------------------------------------------------------------------ #
# Singleton reset                                                        #
# ------------------------------------------------------------------ #

def test_reset_manager_clears_singleton():
    reset_manager()
    from utils.key_manager import _instance
    assert _instance is None


# ------------------------------------------------------------------ #
# Cost tracker rotation recording                                        #
# ------------------------------------------------------------------ #

def test_cost_tracker_records_rotations():
    tracker = CostTracker()
    tracker.record_rotation("****AAA", "****BBB", "RESOURCE_EXHAUSTED")
    tracker.record_rotation("****BBB", "****CCC", "RESOURCE_EXHAUSTED")
    summary = tracker.summary()
    assert summary["total_key_rotations"] == 2


def test_cost_tracker_per_key_requests():
    tracker = CostTracker()
    tracker.record(
        stage="stage_1", law_id="EG_PDPL", model="gemini-2.0-flash",
        input_tokens=1000, output_tokens=500, api_key_suffix="****AAA",
    )
    tracker.record(
        stage="stage_1", law_id="EG_PDPL", model="gemini-2.0-flash",
        input_tokens=800, output_tokens=400, api_key_suffix="****BBB",
    )
    tracker.record(
        stage="stage_1", law_id="EG_PDPL", model="gemini-2.0-flash",
        input_tokens=900, output_tokens=450, api_key_suffix="****AAA",
    )
    summary = tracker.summary()
    assert summary["per_key_requests"]["****AAA"] == 2
    assert summary["per_key_requests"]["****BBB"] == 1


def test_cost_tracker_per_key_failures():
    tracker = CostTracker()
    tracker.record_key_failure("****AAA")
    tracker.record_key_failure("****AAA")
    tracker.record_key_failure("****BBB")
    summary = tracker.summary()
    assert summary["per_key_failures"]["****AAA"] == 2
    assert summary["per_key_failures"]["****BBB"] == 1


# ------------------------------------------------------------------ #
# Settings: key loading                                                 #
# ------------------------------------------------------------------ #

def test_settings_loads_multiple_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "KEY_ONE,KEY_TWO,KEY_THREE")
    import importlib
    import config.settings as s
    importlib.reload(s)
    assert len(s.GEMINI_API_KEYS) == 3
    assert s.GEMINI_API_KEYS[0] == "KEY_ONE"
    assert s.GEMINI_API_KEYS[2] == "KEY_THREE"


def test_settings_strips_whitespace(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", " KEY_A , KEY_B ")
    import importlib
    import config.settings as s
    importlib.reload(s)
    assert s.GEMINI_API_KEYS == ["KEY_A", "KEY_B"]


def test_settings_falls_back_to_single_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "SINGLE_KEY")
    import importlib
    import config.settings as s
    importlib.reload(s)
    assert s.GEMINI_API_KEYS == ["SINGLE_KEY"]
