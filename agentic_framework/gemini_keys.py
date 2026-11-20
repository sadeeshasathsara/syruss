"""
Smart multi-key rotation and availability management for Google Gemini models and embeddings.

Features:
- Maintains a pool of Gemini API keys.
- Smart selection: uses available keys (cooldown expired), prioritizing least-recently-used (LRU)
  and round-robin so it never always starts from key #1.
- Automatic rotation on HTTP 429 (RESOURCE_EXHAUSTED / rate limit) and HTTP 403
  (PERMISSION_DENIED / billing not enabled on that project key): puts the exhausted key into
  cooldown and immediately falls back to the next available key without failing the request.
- Raises AllKeysExhaustedError when every key in the pool has been tried and failed, so the
  outer agent loop does not add a redundant sleep on top of the pool's own cooldown management.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import PrivateAttr
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from agentic_framework.config import settings

logger = logging.getLogger("agent.gemini_keys")

class AllKeysExhaustedError(Exception):
    """
    Raised by RotatingChatGoogleGenerativeAI when every key in the pool has been tried
    and all failed with either a rate-limit or permission error.  The pool has already
    recorded cooldowns internally; callers must NOT add an extra sleep on this error.
    """


_DEFAULT_COOLDOWN_SECONDS = 60.0


@dataclass
class GeminiKeyStatus:
    key: str
    cooldown_until: float = 0.0
    last_used: float = 0.0
    total_calls: int = 0
    total_successes: int = 0
    total_rate_limits: int = 0
    total_errors: int = 0

    @property
    def masked_key(self) -> str:
        k = self.key.strip()
        if len(k) <= 8:
            return "***"
        return f"{k[:4]}...{k[-4:]}"

    def is_available(self, now: Optional[float] = None) -> bool:
        t = time.monotonic() if now is None else now
        return t >= self.cooldown_until

    def remaining_cooldown(self, now: Optional[float] = None) -> float:
        t = time.monotonic() if now is None else now
        return max(0.0, self.cooldown_until - t)


class GeminiKeyPool:
    """Thread-safe and async-safe pool of Gemini keys with availability and LRU tracking."""

    def __init__(self, keys: Sequence[str], default_cooldown: float = _DEFAULT_COOLDOWN_SECONDS):
        # Deduplicate while preserving order
        seen: Set[str] = set()
        clean_keys: List[str] = []
        for k in keys:
            k_clean = k.strip()
            if k_clean and k_clean not in seen:
                seen.add(k_clean)
                clean_keys.append(k_clean)

        self._keys: List[GeminiKeyStatus] = [GeminiKeyStatus(key=k) for k in clean_keys]
        self._default_cooldown = max(1.0, float(default_cooldown))
        self._lock = threading.Lock()
        self._rr_index = 0

    @property
    def keys(self) -> List[GeminiKeyStatus]:
        return list(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def get_best_key(self, exclude_keys: Optional[Set[str]] = None,
                     now: Optional[float] = None) -> Tuple[Optional[GeminiKeyStatus], float]:
        """
        Picks the best key:
        1. Filters out excluded keys.
        2. From available keys (cooldown expired), picks the least-recently used.
           If multiple have never been used (last_used == 0), rotates among them so we don't
           always start from the first key.
        3. If no keys are currently available (all in cooldown), picks the one with the
           shortest remaining cooldown.
        Returns (key_status, remaining_wait_seconds).
        """
        with self._lock:
            if not self._keys:
                return None, 0.0

            t = time.monotonic() if now is None else now
            candidates = [k for k in self._keys if not exclude_keys or k.key not in exclude_keys]
            if not candidates:
                # If all were excluded, fall back to any key
                candidates = self._keys

            available = [k for k in candidates if k.is_available(t)]

            if available:
                # Among available keys:
                # If there are keys that haven't been used yet (last_used == 0), round-robin them
                unused = [k for k in available if k.last_used == 0.0]
                if unused:
                    chosen = unused[self._rr_index % len(unused)]
                    self._rr_index = (self._rr_index + 1) % len(unused)
                    return chosen, 0.0

                # Otherwise, select the Least Recently Used (oldest last_used timestamp)
                chosen = min(available, key=lambda k: k.last_used)
                return chosen, 0.0

            # No keys available right now: pick the one whose cooldown expires soonest
            soonest = min(candidates, key=lambda k: k.cooldown_until)
            return soonest, soonest.remaining_cooldown(t)

    def record_rate_limit(self, key: str, delay: Optional[float] = None) -> float:
        """Puts key into cooldown."""
        with self._lock:
            for k in self._keys:
                if k.key == key:
                    cooldown = delay if (delay is not None and delay > 0) else self._default_cooldown
                    k.cooldown_until = time.monotonic() + cooldown
                    k.total_rate_limits += 1
                    logger.warning(
                        "Gemini key %s rate-limited (cooling down for %.1fs). Available keys left in pool: %d/%d",
                        k.masked_key, cooldown,
                        sum(1 for x in self._keys if x.is_available()), len(self._keys)
                    )
                    return cooldown
        return self._default_cooldown

    def record_success(self, key: str) -> None:
        """Marks key as recently used and successful."""
        with self._lock:
            for k in self._keys:
                if k.key == key:
                    k.last_used = time.monotonic()
                    k.total_successes += 1
                    k.total_calls += 1
                    break

    def record_error(self, key: str) -> None:
        """Tracks generic non-rate-limit errors."""
        with self._lock:
            for k in self._keys:
                if k.key == key:
                    k.total_errors += 1
                    k.total_calls += 1
                    break

    @staticmethod
    def is_rate_limit_error(exc: Exception) -> Tuple[bool, Optional[float]]:
        """
        Determines if an exception represents a rate limit / quota exhaustion,
        and parses any recommended retry delay in seconds.
        """
        text = f"{type(exc).__name__} {exc}"

        # Check for 429 / 403 status code on exception attributes if present
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code in (429, "429", 403, "403"):
            is_rl = True
        else:
            is_rl = bool(re.search(
                # 429 rate-limits, quota exhaustion, AND 403 permission / billing issues:
                # a 403 on a specific key means that key's GCP project has billing not enabled
                # or the Gemini API is not activated — rotating to another key is correct.
                r"\b429\b|RESOURCE_EXHAUSTED|RateLimit|rate_limit|rate limit|quota|QuotaExceeded|TooManyRequests"
                r"|\b403\b|PERMISSION_DENIED",
                text, re.IGNORECASE
            ))

        if not is_rl:
            return False, None

        # Look for explicit retry hints in error message:
        # e.g., "retry in 14.5s", "retryDelay: 15s", "Please retry after 20 seconds"
        hint = (
            re.search(r"retry in ([\d.]+)s", text, re.IGNORECASE)
            or re.search(r"retryDelay'?:\s*'?(\d+)s", text, re.IGNORECASE)
            or re.search(r"retry after ([\d.]+) second", text, re.IGNORECASE)
        )
        if hint:
            try:
                seconds = float(hint.group(1))
                return True, min(120.0, max(1.0, seconds + 1.0))
            except ValueError:
                pass

        return True, None


class RotatingChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """
    Subclass of ChatGoogleGenerativeAI that rotates across a pool of Gemini API keys.
    Maintains full compatibility with bind_tools, temperature, etc.
    """
    _pool: Optional[GeminiKeyPool] = PrivateAttr(default=None)
    _delegates: Dict[str, ChatGoogleGenerativeAI] = PrivateAttr(default_factory=dict)
    _max_retries_per_key: int = PrivateAttr(default=1)

    def __init__(self, pool: Optional[GeminiKeyPool] = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._pool = pool
        self._delegates = {}
        self._max_retries_per_key = kwargs.get("max_retries", 1)

    def _get_delegate(self, api_key: str) -> ChatGoogleGenerativeAI:
        if api_key not in self._delegates:
            self._delegates[api_key] = ChatGoogleGenerativeAI(
                model=self.model,
                google_api_key=api_key,
                temperature=self.temperature,
                max_retries=self._max_retries_per_key,
            )
        return self._delegates[api_key]

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if not self._pool or len(self._pool) <= 1:
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

        tried_keys: Set[str] = set()
        max_attempts = len(self._pool)

        for attempt in range(max_attempts):
            key_status, wait = self._pool.get_best_key(exclude_keys=tried_keys)
            if not key_status:
                break

            if wait > 0 and len(tried_keys) >= len(self._pool):
                # All keys in the pool have been tried and are in cooldown
                if wait > 30.0:
                    logger.warning("All Gemini keys are in cooldown (soonest in %.1fs); letting outer loop wait", wait)
                    # Allow outer loop or deadline to handle long wait
                    pass
                else:
                    logger.info("All Gemini keys cooling down; waiting %.1fs before next attempt", wait)
                    await asyncio.sleep(wait)
                    key_status, _ = self._pool.get_best_key()
                    if not key_status:
                        break

            tried_keys.add(key_status.key)
            delegate = self._get_delegate(key_status.key)

            try:
                res = await delegate._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
                self._pool.record_success(key_status.key)
                return res
            except Exception as exc:
                is_rl, delay = self._pool.is_rate_limit_error(exc)
                if is_rl:
                    self._pool.record_rate_limit(key_status.key, delay)
                    if len(tried_keys) < len(self._pool):
                        logger.info("Retrying with another Gemini key from pool (attempt %d/%d)...",
                                    attempt + 1, max_attempts)
                    # Whether or not there are more keys to try, continue the for-loop so
                    # AllKeysExhaustedError is raised below instead of the raw API error.
                    continue
                # Non-rotatable error (e.g. invalid prompt, server error): propagate immediately.
                self._pool.record_error(key_status.key)
                raise

        # All keys have been tried (or the pool returned no key); raising AllKeysExhaustedError
        # tells the outer agent loop not to add an extra sleep — the pool already tracked
        # cooldowns internally.
        raise AllKeysExhaustedError(
            f"All {len(self._pool)} Gemini keys in the pool have been tried and failed "
            "(rate-limited or permission-denied). The pool will recover as keys cool down."
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if not self._pool or len(self._pool) <= 1:
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        tried_keys: Set[str] = set()
        max_attempts = len(self._pool)

        for attempt in range(max_attempts):
            key_status, wait = self._pool.get_best_key(exclude_keys=tried_keys)
            if not key_status:
                break

            if wait > 0 and len(tried_keys) >= len(self._pool):
                if wait <= 30.0:
                    time.sleep(wait)
                    key_status, _ = self._pool.get_best_key()
                    if not key_status:
                        break

            tried_keys.add(key_status.key)
            delegate = self._get_delegate(key_status.key)

            try:
                res = delegate._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
                self._pool.record_success(key_status.key)
                return res
            except Exception as exc:
                is_rl, delay = self._pool.is_rate_limit_error(exc)
                if is_rl:
                    self._pool.record_rate_limit(key_status.key, delay)
                    # Continue so AllKeysExhaustedError is raised below, not the raw API error.
                    continue
                # Non-rotatable error: propagate immediately.
                self._pool.record_error(key_status.key)
                raise

        raise AllKeysExhaustedError(
            f"All {len(self._pool)} Gemini keys in the pool have been tried and failed "
            "(rate-limited or permission-denied). The pool will recover as keys cool down."
        )


class RotatingGoogleGenerativeAIEmbeddings(GoogleGenerativeAIEmbeddings):
    """Rotating wrapper for Gemini embeddings."""
    _pool: Optional[GeminiKeyPool] = PrivateAttr(default=None)
    _delegates: Dict[str, GoogleGenerativeAIEmbeddings] = PrivateAttr(default_factory=dict)

    def __init__(self, pool: Optional[GeminiKeyPool] = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._pool = pool
        self._delegates = {}

    def _get_delegate(self, api_key: str) -> GoogleGenerativeAIEmbeddings:
        if api_key not in self._delegates:
            self._delegates[api_key] = GoogleGenerativeAIEmbeddings(
                model=self.model,
                google_api_key=api_key,
            )
        return self._delegates[api_key]

    async def aembed_query(self, text: str, **kwargs: Any) -> List[float]:
        if not self._pool or len(self._pool) <= 1:
            return await super().aembed_query(text, **kwargs)

        tried_keys: Set[str] = set()
        for _ in range(len(self._pool)):
            key_status, _ = self._pool.get_best_key(exclude_keys=tried_keys)
            if not key_status:
                break
            tried_keys.add(key_status.key)
            delegate = self._get_delegate(key_status.key)
            try:
                res = await delegate.aembed_query(text, **kwargs)
                self._pool.record_success(key_status.key)
                return res
            except Exception as exc:
                is_rl, delay = self._pool.is_rate_limit_error(exc)
                if is_rl:
                    self._pool.record_rate_limit(key_status.key, delay)
                    if len(tried_keys) < len(self._pool):
                        continue
                self._pool.record_error(key_status.key)
                raise
        return await super().aembed_query(text, **kwargs)

    async def aembed_documents(self, texts: List[str], **kwargs: Any) -> List[List[float]]:
        if not self._pool or len(self._pool) <= 1:
            return await super().aembed_documents(texts, **kwargs)

        tried_keys: Set[str] = set()
        for _ in range(len(self._pool)):
            key_status, _ = self._pool.get_best_key(exclude_keys=tried_keys)
            if not key_status:
                break
            tried_keys.add(key_status.key)
            delegate = self._get_delegate(key_status.key)
            try:
                res = await delegate.aembed_documents(texts, **kwargs)
                self._pool.record_success(key_status.key)
                return res
            except Exception as exc:
                is_rl, delay = self._pool.is_rate_limit_error(exc)
                if is_rl:
                    self._pool.record_rate_limit(key_status.key, delay)
                    if len(tried_keys) < len(self._pool):
                        continue
                self._pool.record_error(key_status.key)
                raise
        return await super().aembed_documents(texts, **kwargs)


_GLOBAL_POOL: Optional[GeminiKeyPool] = None
_GLOBAL_POOL_KEYS: Tuple[str, ...] = ()


def get_gemini_key_pool() -> Optional[GeminiKeyPool]:
    """Returns or initializes the singleton GeminiKeyPool from settings."""
    global _GLOBAL_POOL, _GLOBAL_POOL_KEYS
    keys = tuple(settings.gemini_api_keys)
    if not keys:
        return None
    if _GLOBAL_POOL is None or _GLOBAL_POOL_KEYS != keys:
        _GLOBAL_POOL_KEYS = keys
        cooldown = getattr(settings, "GEMINI_COOLDOWN_SECONDS", _DEFAULT_COOLDOWN_SECONDS)
        _GLOBAL_POOL = GeminiKeyPool(keys, default_cooldown=cooldown)
    return _GLOBAL_POOL


def create_chat_gemini(model: str, temperature: float, pool: Optional[GeminiKeyPool] = None,
                       **kwargs: Any) -> ChatGoogleGenerativeAI:
    """Creates a ChatGoogleGenerativeAI or RotatingChatGoogleGenerativeAI based on available keys."""
    p = pool or get_gemini_key_pool()
    best_key = p.get_best_key()[0].key if (p and p.keys) else (settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY or "")
    if p and len(p) > 1:
        return RotatingChatGoogleGenerativeAI(
            model=model,
            google_api_key=best_key,
            temperature=temperature,
            pool=p,
            **kwargs,
        )
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=best_key,
        temperature=temperature,
        **kwargs,
    )


def create_embeddings_gemini(model: str, pool: Optional[GeminiKeyPool] = None,
                             **kwargs: Any) -> GoogleGenerativeAIEmbeddings:
    """Creates a GoogleGenerativeAIEmbeddings or RotatingGoogleGenerativeAIEmbeddings."""
    p = pool or get_gemini_key_pool()
    best_key = p.get_best_key()[0].key if (p and p.keys) else (settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY or "")
    if p and len(p) > 1:
        return RotatingGoogleGenerativeAIEmbeddings(
            model=model,
            google_api_key=best_key,
            pool=p,
            **kwargs,
        )
    return GoogleGenerativeAIEmbeddings(
        model=model,
        google_api_key=best_key,
        **kwargs,
    )
