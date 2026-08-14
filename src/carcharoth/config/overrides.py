"""Apply dot-path parameter overrides to a raw (pre-validation) config dict.

Overrides are applied to the raw YAML dict and the result is re-validated
with ``AppConfig.model_validate``, so every trial's combination passes the
full Pydantic validation.

Strictness matters here: AppConfig does not forbid extra keys, so a typo'd
path would silently no-op and the study would "optimize" a parameter that
does nothing. Intermediate path segments must therefore already exist; only
the leaf key may be absent (schema fields with defaults).
"""

import copy
from collections.abc import Iterable, Mapping
from typing import Any


class OverrideError(ValueError):
    """A dot-path does not lead into the config structure."""


def apply_overrides(raw: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``raw`` with each ``dot.path -> value`` set."""
    result = copy.deepcopy(raw)
    for path, value in overrides.items():
        _walk_to_parent(result, path)[path.rsplit(".", 1)[-1]] = value
    return result


def validate_override_paths(raw: dict[str, Any], paths: Iterable[str]) -> None:
    """Fail fast (before any trial runs) if a search-space path is invalid."""
    for path in paths:
        _walk_to_parent(raw, path)


def _walk_to_parent(raw: dict[str, Any], path: str) -> dict[str, Any]:
    """The dict holding the leaf key; every intermediate segment must exist."""
    node: Any = raw
    segments = path.split(".")
    for depth, segment in enumerate(segments[:-1]):
        if not isinstance(node, dict) or segment not in node:
            prefix = ".".join(segments[: depth + 1])
            raise OverrideError(f"invalid override path {path!r}: {prefix!r} not in config")
        node = node[segment]
    if not isinstance(node, dict):
        raise OverrideError(f"invalid override path {path!r}: parent is not a mapping")
    return node
