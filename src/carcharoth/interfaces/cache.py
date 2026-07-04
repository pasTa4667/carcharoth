from typing import Any, Protocol


class Cache(Protocol):
    """Optional read-through cache slot for frequently read data.

    v1 ships only NoOpCache; a real implementation (in-memory, Redis) can be
    swapped in at the composition root without touching any service.
    """

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_seconds: float) -> None: ...
