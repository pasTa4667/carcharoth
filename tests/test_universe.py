from datetime import UTC, date, datetime, timedelta

import numpy as np

from carcharoth.config.universe_config import UniverseDiscoveryConfig
from carcharoth.domain.models import Bar
from carcharoth.universe import discover, load_reuse_inputs, load_snapshot, publish_snapshot


def series(symbol: str, days: list[date], seed: int, volatility: float, volume: float) -> list[Bar]:
    random = np.random.default_rng(seed)
    close = 100.0
    bars = []
    for index, day in enumerate(days):
        previous = close
        close *= float(np.exp(random.normal(0.0005, volatility)))
        gap = float(random.normal(0, volatility / 3))
        open_price = previous * np.exp(gap)
        spread = volatility * (1.5 + index % 3 / 10)
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=datetime(day.year, day.month, day.day, 17, tzinfo=UTC),
                open=open_price,
                high=max(open_price, close) * (1 + spread),
                low=min(open_price, close) * (1 - spread),
                close=close,
                volume=volume,
            )
        )
    return bars


def config(**overrides: object) -> UniverseDiscoveryConfig:
    values = dict(
        start=date(2016, 1, 1),
        end=date(2016, 2, 29),
        top_n=5,
        min_observations=10,
        min_coverage=0.8,
        min_bucket_size=2,
        distance_threshold=2.0,
    )
    values.update(overrides)
    return UniverseDiscoveryConfig.model_validate(values)


def test_discovery_is_deterministic_and_complete_linkage_is_bounded() -> None:
    days = [date(2016, 1, 4) + timedelta(days=index) for index in range(40)]
    days = [day for day in days if day.weekday() < 5]
    bars = {
        "SPY": series("SPY", days, 1, 0.01, 1_000_000),
        "A": series("A", days, 2, 0.01, 900_000),
        "B": series("B", days, 3, 0.011, 850_000),
        "C": series("C", days, 4, 0.04, 200_000),
        "D": series("D", days, 5, 0.045, 180_000),
    }
    first = discover(bars, days, config())
    second = discover(dict(reversed(list(bars.items()))), days, config())
    assert first.buckets == second.buckets
    assert first.buckets["all"] == sorted(bars)
    positions = {feature.symbol: index for index, feature in enumerate(first.features)}
    for members in first.buckets.values():
        if members is first.buckets["all"]:
            continue
        for left in members:
            for right in members:
                assert first.distances[positions[left], positions[right]] <= 2.0 + 1e-12


def test_missing_history_is_reported_and_not_filled() -> None:
    days = [date(2016, 1, 4) + timedelta(days=index) for index in range(20)]
    bars = {
        "SPY": series("SPY", days, 1, 0.01, 1_000_000),
        "A": series("A", days, 2, 0.02, 500_000),
        "SHORT": series("SHORT", days[:3], 3, 0.02, 500_000),
    }
    result = discover(bars, days, config(top_n=2, min_bucket_size=2))
    assert result.exclusions["SHORT"] == "insufficient_observations"
    assert result.buckets["all"] == ["A", "SPY"]


def test_snapshot_is_immutable_checksummed_and_reusable(tmp_path) -> None:
    days = [date(2016, 1, 4) + timedelta(days=index) for index in range(20)]
    bars = {
        "SPY": series("SPY", days, 1, 0.01, 1_000_000),
        "A": series("A", days, 2, 0.02, 500_000),
    }
    discovery_config = config(top_n=2, min_bucket_size=2, output_root=tmp_path)
    result = discover(bars, days, discovery_config)
    path = publish_snapshot(
        discovery_config,
        result,
        [{"id": "1", "symbol": "A", "name": "A", "exchange": "NYSE"}],
        days,
        bars,
    )
    assert load_snapshot(path).buckets["all"] == ["A", "SPY"]
    assets, loaded_days, loaded_bars, retrieved_at = load_reuse_inputs(path, discovery_config)
    assert assets[0]["symbol"] == "A"
    assert retrieved_at.tzinfo is not None
    assert loaded_days == days
    assert len(loaded_bars["A"]) == len(days)

    (path / "calendar.json").write_text("[]")
    try:
        load_reuse_inputs(path, discovery_config)
    except ValueError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("tampered inputs must fail")
