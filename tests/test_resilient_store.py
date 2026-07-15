"""ResilientByteStore: one warning, then a permanent no-op short-circuit."""

import logging

import pytest

from carcharoth.services.cache.resilient import ResilientByteStore
from tests.fakes import InMemoryByteStore, RaisingByteStore


def test_delegates_while_healthy() -> None:
    inner = InMemoryByteStore()
    store = ResilientByteStore(inner)
    store.set("carch:x", b"1")
    store.mset({"carch:y": b"2"})
    assert store.get("carch:x") == b"1"
    assert store.mget(["carch:x", "carch:y", "missing"]) == [b"1", b"2", None]
    assert store.count_prefix("carch:") == 2
    assert store.used_memory_bytes() == 2
    assert store.delete_prefix("carch:") == 2


def test_first_failure_warns_once_and_short_circuits(caplog: pytest.LogCaptureFixture) -> None:
    inner = RaisingByteStore()
    store = ResilientByteStore(inner)
    with caplog.at_level(logging.WARNING):
        assert store.get("k") is None
        assert store.mget(["a", "b"]) == [None, None]
        store.set("k", b"v")
        store.mset({"k": b"v"})
        assert store.count_prefix("carch:") == 0
        assert store.delete_prefix("carch:") == 0
        assert store.used_memory_bytes() is None
    assert inner.calls == 1  # everything after the first failure short-circuits
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_write_failure_also_disables_reads() -> None:
    inner = RaisingByteStore()
    store = ResilientByteStore(inner)
    store.set("k", b"v")
    assert store.get("k") is None
    assert inner.calls == 1
