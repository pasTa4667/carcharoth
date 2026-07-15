"""Redis-backed ByteStore — the only module that imports ``redis``.

A 1:1 protocol mapping with no logic of its own; like the other SDK
boundaries it is spot-checked manually rather than unit-tested. Values are
pickles produced exclusively by carcharoth against a local compose-network
Redis — never point REDIS_URL at a shared or untrusted server.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import cast

import redis

from carcharoth.interfaces.cache import ByteStore
from carcharoth.services.cache.resilient import ResilientByteStore

logger = logging.getLogger(__name__)

_SCAN_BATCH = 500


class RedisByteStore:
    """Implements ByteStore over a connected redis client (bytes responses)."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def get(self, key: str) -> bytes | None:
        return cast("bytes | None", self._client.get(key))

    def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        if not keys:
            return []
        return cast("list[bytes | None]", self._client.mget(list(keys)))

    def set(self, key: str, value: bytes) -> None:
        self._client.set(key, value)

    def mset(self, items: Mapping[str, bytes]) -> None:
        if items:
            self._client.mset(dict(items))

    def count_prefix(self, prefix: str) -> int:
        return sum(1 for _ in self._client.scan_iter(match=prefix + "*", count=_SCAN_BATCH))

    def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        batch: list[bytes] = []
        for key in self._client.scan_iter(match=prefix + "*", count=_SCAN_BATCH):
            batch.append(key)
            if len(batch) >= _SCAN_BATCH:
                deleted += self._client.delete(*batch)
                batch = []
        if batch:
            deleted += self._client.delete(*batch)
        return deleted

    def used_memory_bytes(self) -> int | None:
        info = cast("dict[str, object]", self._client.info("memory"))
        used = info.get("used_memory")
        return int(cast(int, used)) if used is not None else None


def build_redis_store(url: str, *, resilient: bool = True) -> ByteStore | None:
    """Connect and PING; unreachable -> one warning and None (run uncached).

    ``resilient=True`` wraps the store so mid-run failures degrade instead
    of raising; the ``cache`` CLI passes False to surface errors directly.
    """
    client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=5)
    try:
        client.ping()
    except (redis.RedisError, OSError) as exc:
        logger.warning("redis unreachable at %s (%s) — running without persistent cache", url, exc)
        return None
    store = RedisByteStore(client)
    return ResilientByteStore(store) if resilient else store
