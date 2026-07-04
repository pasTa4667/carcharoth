from typing import Any


class NoOpCache:
    """Cache implementation that caches nothing (the v1 default)."""

    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        pass
