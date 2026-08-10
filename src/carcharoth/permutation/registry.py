"""Permutation method registry: maps config names to method factories.

Mirrors ``strategies/registry.py``. Adding a variant (walk-forward bars,
monte-carlo trade shuffle, ...): implement the PermutationMethod protocol in a
new module under ``permutation/methods/`` and add one entry here. Nothing else
in the codebase changes.
"""

from collections.abc import Callable
from typing import Any

from carcharoth.interfaces.permutation import PermutationMethod
from carcharoth.permutation.methods.in_sample_bars import InSampleBarsPermutation

PERMUTATION_METHODS: dict[str, Callable[..., PermutationMethod]] = {
    InSampleBarsPermutation.name: InSampleBarsPermutation,
}


def build_permutation_method(name: str, params: dict[str, Any]) -> PermutationMethod:
    try:
        factory = PERMUTATION_METHODS[name]
    except KeyError:
        available = ", ".join(sorted(PERMUTATION_METHODS))
        raise ValueError(f"unknown permutation method {name!r}; available: {available}") from None
    return factory(**params)
