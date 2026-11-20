"""Tests for Gemini multi-key rotation, smart availability, and automatic failover."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agentic_framework.config import AgentSettings
from agentic_framework.gemini_keys import (
    AllKeysExhaustedError,
    GeminiKeyPool,
    GeminiKeyStatus,
    RotatingChatGoogleGenerativeAI,
    create_chat_gemini,
)


def test_gemini_api_keys_config_parsing():
    # Comma-separated
    s = AgentSettings(GEMINI_API_KEYS="key1, key2, key3", GEMINI_API_KEY=None, GOOGLE_API_KEY=None)
    assert s.gemini_api_keys == ["key1", "key2", "key3"]

    # JSON array
    s = AgentSettings(GEMINI_API_KEYS='["keyA", "keyB"]', GEMINI_API_KEY=None, GOOGLE_API_KEY=None)
    assert s.gemini_api_keys == ["keyA", "keyB"]

    # Deduplication and fallback to single keys
    s = AgentSettings(GEMINI_API_KEYS="key1, key2", GEMINI_API_KEY="key2", GOOGLE_API_KEY="key3")
    assert s.gemini_api_keys == ["key1", "key2", "key3"]

    # Empty
    s = AgentSettings(GEMINI_API_KEYS=None, GEMINI_API_KEY=None, GOOGLE_API_KEY=None)
    assert s.gemini_api_keys == []


def test_gemini_key_pool_smart_selection_does_not_always_start_from_first():
    pool = GeminiKeyPool(["key_a", "key_b", "key_c"])
    assert len(pool) == 3

    # On consecutive fresh acquisitions, unused keys are rotated via round-robin
    first, wait1 = pool.get_best_key()
    second, wait2 = pool.get_best_key()
    third, wait3 = pool.get_best_key()

    assert wait1 == 0.0 and wait2 == 0.0 and wait3 == 0.0
    # Must rotate through distinct keys rather than repeatedly picking index 0
    assert {first.key, second.key, third.key} == {"key_a", "key_b", "key_c"}


def test_gemini_key_pool_lru_selection():
    pool = GeminiKeyPool(["key_a", "key_b", "key_c"])
    t = time.monotonic()

    # Suppose key_a was used 100s ago, key_b 50s ago, key_c 10s ago
    pool._keys[0].last_used = t - 100.0
    pool._keys[1].last_used = t - 50.0
    pool._keys[2].last_used = t - 10.0

    # Best key should be key_a because it is the least-recently used
    best, wait = pool.get_best_key(now=t)
    assert best.key == "key_a"
    assert wait == 0.0

    # Once key_a is used (updated to now), key_b should become the best key
    pool.record_success("key_a")
    best2, _ = pool.get_best_key()
    assert best2.key == "key_b"


def test_gemini_key_pool_skips_keys_in_cooldown():
    pool = GeminiKeyPool(["key_a", "key_b", "key_c"], default_cooldown=60.0)

    # Put key_a into cooldown
    pool.record_rate_limit("key_a", delay=60.0)
    assert not pool._keys[0].is_available()

    # Request best key: key_a must be skipped!
    best, wait = pool.get_best_key()
    assert best.key in ("key_b", "key_c")
    assert wait == 0.0

    # Put key_b into cooldown as well
    pool.record_rate_limit("key_b", delay=60.0)
    best2, wait2 = pool.get_best_key()
    assert best2.key == "key_c"
    assert wait2 == 0.0


def test_gemini_key_pool_all_in_cooldown_picks_soonest():
    pool = GeminiKeyPool(["key_a", "key_b"], default_cooldown=60.0)
    t = time.monotonic()

    # key_a cools down in 10s, key_b cools down in 40s
    pool._keys[0].cooldown_until = t + 10.0
    pool._keys[1].cooldown_until = t + 40.0

    best, wait = pool.get_best_key(now=t)
    assert best.key == "key_a"
    assert 9.0 <= wait <= 10.5


def test_is_rate_limit_error_parsing():
    pool = GeminiKeyPool(["key1"])

    # 429 string
    is_rl, delay = pool.is_rate_limit_error(RuntimeError("HTTP 429: Too Many Requests. Please retry in 15s"))
    assert is_rl is True
    assert delay == 16.0

    # RESOURCE_EXHAUSTED
    is_rl2, delay2 = pool.is_rate_limit_error(Exception("RESOURCE_EXHAUSTED: Quota exceeded for quota metric"))
    assert is_rl2 is True
    assert delay2 is None

    # Non-rate-limit error
    is_rl3, delay3 = pool.is_rate_limit_error(ValueError("Invalid argument: prompt is too long"))
    assert is_rl3 is False
    assert delay3 is None


@pytest.mark.asyncio
async def test_rotating_chat_gemini_failover():
    pool = GeminiKeyPool(["key1", "key2"])
    model = RotatingChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key="key1", pool=pool)

    # Mock delegates
    mock_del1 = MagicMock()
    mock_del1._agenerate = AsyncMock(side_effect=Exception("429 RESOURCE_EXHAUSTED: retry in 5s"))

    mock_del2 = MagicMock()
    expected_result = ChatResult(generations=[ChatGeneration(message=AIMessage(content="Hello from key2!"))])
    mock_del2._agenerate = AsyncMock(return_value=expected_result)

    def fake_get_delegate(api_key: str):
        return mock_del1 if api_key == "key1" else mock_del2

    with patch.object(model, "_get_delegate", side_effect=fake_get_delegate):
        result = await model._agenerate([HumanMessage(content="Hi")])
        assert result == expected_result
        # Check that key1 was marked in cooldown
        k1 = next(k for k in pool.keys if k.key == "key1")
        k2 = next(k for k in pool.keys if k.key == "key2")
        assert k1.total_rate_limits == 1
        assert k2.total_successes == 1

def test_is_rate_limit_error_includes_403_permission_denied():
    """403 PERMISSION_DENIED (billing not enabled) must be treated as a rotation trigger."""
    pool = GeminiKeyPool(["key1"])

    # HTTP 403 string in message
    is_rl, _ = pool.is_rate_limit_error(RuntimeError("403 PERMISSION_DENIED: billing not enabled"))
    assert is_rl is True

    # status_code attribute == 403
    exc = RuntimeError("some error")
    exc.status_code = 403  # type: ignore[attr-defined]
    is_rl2, _ = pool.is_rate_limit_error(exc)
    assert is_rl2 is True

    # code attribute == 403
    exc2 = RuntimeError("some error")
    exc2.code = 403  # type: ignore[attr-defined]
    is_rl3, _ = pool.is_rate_limit_error(exc2)
    assert is_rl3 is True

    # Plain 400 / other non-rl errors must still return False
    is_rl4, _ = pool.is_rate_limit_error(RuntimeError("400 Bad Request: invalid argument"))
    assert is_rl4 is False


@pytest.mark.asyncio
async def test_rotating_chat_gemini_raises_all_keys_exhausted():
    """When every key in the pool fails with PERMISSION_DENIED, AllKeysExhaustedError is raised."""
    pool = GeminiKeyPool(["key1", "key2"])
    model = RotatingChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key="key1", pool=pool)

    perm_err = Exception("403 PERMISSION_DENIED: billing not enabled")
    mock_del = MagicMock()
    mock_del._agenerate = AsyncMock(side_effect=perm_err)

    with patch.object(model, "_get_delegate", return_value=mock_del):
        with pytest.raises(AllKeysExhaustedError):
            await model._agenerate([HumanMessage(content="Hi")])

    # Both keys should have been put into cooldown
    assert all(k.total_rate_limits == 1 for k in pool.keys)
