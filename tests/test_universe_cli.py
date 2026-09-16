from datetime import UTC, date, datetime, timedelta

import yaml

from carcharoth.config.universe_config import UniverseDiscoveryConfig
from carcharoth.domain.models import Bar
from carcharoth.main import main
from carcharoth.universe import discover, publish_snapshot


def bars(symbol: str, days: list[date], scale: float) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timestamp=datetime(day.year, day.month, day.day, 17, tzinfo=UTC),
            open=100 + index * scale,
            high=104 + index * scale,
            low=99 + index * scale,
            close=101 + index * scale + (index % 3),
            volume=1_000_000 / scale,
        )
        for index, day in enumerate(days)
    ]


def test_reuse_cli_needs_no_services(tmp_path, monkeypatch, capsys) -> None:
    days = [date(2016, 1, 4) + timedelta(days=index) for index in range(20)]
    source_root = tmp_path / "source"
    config = UniverseDiscoveryConfig(
        start=days[0],
        end=days[-1],
        top_n=2,
        min_observations=10,
        min_coverage=0.8,
        min_bucket_size=2,
        distance_threshold=10,
        output_root=source_root,
    )
    source_bars = {"SPY": bars("SPY", days, 1), "A": bars("A", days, 2)}
    source = publish_snapshot(config, discover(source_bars, days, config), [], days, source_bars)

    destination = tmp_path / "destination"
    raw = config.model_dump(mode="json")
    raw["output_root"] = str(destination)
    config_path = tmp_path / "discovery.yaml"
    config_path.write_text(yaml.safe_dump(raw))
    monkeypatch.setattr("carcharoth.main.Settings", lambda: (_ for _ in ()).throw(AssertionError()))

    main(["universe", "discover", "--config", str(config_path), "--reuse-data", str(source)])
    assert "universe snapshot:" in capsys.readouterr().out
    assert len(list(destination.glob("*/buckets.yaml"))) == 1
