"""Failure-isolating ByteStore wrapper.

The persistent cache is an optimization, never a dependency: the first
failure is logged and the store permanently degrades to a no-op (reads
miss, writes are dropped), so a Redis that dies mid-run doesn't add a
network timeout to every subsequent call.
"""

import logging
from collections.abc import Mapping, Sequence

from carcharoth.interfaces.cache import ByteStore

logger = logging.getLogger(__name__)


class ResilientByteStore:
    """Implements ByteStore; delegates until the inner store first fails."""

    def __init__(self, inner: ByteStore) -> None:
        self._inner = inner
        self._failed = False

    def get(self, key: str) -> bytes | None:
        if self._failed:
            return None
        try:
            return self._inner.get(key)
        except Exception:
            self._disable()
            return None

    def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        if self._failed:
            return [None] * len(keys)
        try:
            return self._inner.mget(keys)
        except Exception:
            self._disable()
            return [None] * len(keys)

    def set(self, key: str, value: bytes) -> None:
        if self._failed:
            return
        try:
            self._inner.set(key, value)
        except Exception:
            self._disable()

    def mset(self, items: Mapping[str, bytes]) -> None:
        if self._failed:
            return
        try:
            self._inner.mset(items)
        except Exception:
            self._disable()

    def count_prefix(self, prefix: str) -> int:
        if self._failed:
            return 0
        try:
            return self._inner.count_prefix(prefix)
        except Exception:
            self._disable()
            return 0

    def delete_prefix(self, prefix: str) -> int:
        if self._failed:
            return 0
        try:
            return self._inner.delete_prefix(prefix)
        except Exception:
            self._disable()
            return 0

    def used_memory_bytes(self) -> int | None:
        if self._failed:
            return None
        try:
            return self._inner.used_memory_bytes()
        except Exception:
            self._disable()
            return None

    def _disable(self) -> None:
        self._failed = True
        logger.warning("persistent cache failed — continuing without it", exc_info=True)
