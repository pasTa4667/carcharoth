from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class Cache(Protocol):
    """Optional read-through cache slot for frequently read data.

    v1 ships only NoOpCache; a real implementation (in-memory, Redis) can be
    swapped in at the composition root without touching any service.
    """

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_seconds: float) -> None: ...


class ByteStore(Protocol):
    """Persistent bytes key/value store (Redis in production, a dict in tests).

    Backs the cross-run caches (historical bars, HMM fits). Serialization is
    the caller's concern; keys are namespaced by prefix (``carch:bars:``, ...)
    so entire caches can be counted and cleared.
    """

    def get(self, key: str) -> bytes | None: ...

    def mget(self, keys: Sequence[str]) -> list[bytes | None]: ...

    def set(self, key: str, value: bytes) -> None: ...

    def mset(self, items: Mapping[str, bytes]) -> None: ...

    def count_prefix(self, prefix: str) -> int: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def used_memory_bytes(self) -> int | None:
        """Total memory used by the store, or None when unknown."""
        ...
