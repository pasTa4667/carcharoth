"""Offline, deterministic universe discovery and immutable snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from carcharoth.config.universe_config import (
    DESCRIPTOR_NAMES,
    UniverseDiscoveryConfig,
    UniverseSnapshot,
    load_universe_snapshot,
    snapshot_digest,
)
from carcharoth.domain.models import Bar

_MARKET_TZ = ZoneInfo("America/New_York")
_FLOOR = 1e-8


@dataclass(frozen=True, slots=True)
class FeatureRecord:
    symbol: str
    rank: int
    descriptors: dict[str, float]
    standardized: dict[str, float]
    cluster: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    features: tuple[FeatureRecord, ...]
    buckets: dict[str, list[str]]
    undersized_clusters: list[list[str]]
    scaling: dict[str, dict[str, float]]
    exclusions: dict[str, str]
    dropped_descriptors: tuple[str, ...]
    distances: np.ndarray


def _bar_date(bar: Bar) -> date:
    return bar.timestamp.astimezone(_MARKET_TZ).date()


def _clean_bars(bars: Sequence[Bar], sessions: set[date]) -> tuple[dict[date, Bar], str | None]:
    clean: dict[date, Bar] = {}
    for bar in bars:
        day = _bar_date(bar)
        if day not in sessions:
            continue
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return {}, "invalid_ohlcv"
        if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close):
            return {}, "invalid_ohlcv"
        previous = clean.get(day)
        if previous is not None and previous != bar:
            return {}, "conflicting_duplicate_session"
        clean[day] = bar
    return clean, None


def _return_series(clean: Mapping[date, Bar], calendar: Sequence[date]) -> dict[date, float]:
    result: dict[date, float] = {}
    for previous, current in pairwise(calendar):
        before, after = clean.get(previous), clean.get(current)
        if before is not None and after is not None:
            result[current] = math.log(after.close / before.close)
    return result


def _descriptors(
    clean: Mapping[date, Bar],
    calendar: Sequence[date],
    benchmark_returns: Mapping[date, float],
) -> dict[str, float] | None:
    ordered = [clean[day] for day in calendar if day in clean]
    returns = _return_series(clean, calendar)
    aligned_days = sorted(set(returns) & set(benchmark_returns))
    if len(returns) < 3 or len(aligned_days) < 2:
        return None
    values = np.asarray(list(returns.values()), dtype=float)
    asset_aligned = np.asarray([returns[day] for day in aligned_days], dtype=float)
    market_aligned = np.asarray([benchmark_returns[day] for day in aligned_days], dtype=float)
    market_variance = float(np.var(market_aligned, ddof=1))
    variance = float(np.var(values, ddof=1))
    if variance <= 0 or market_variance <= 0:
        return None
    lag_pairs = [
        (returns[calendar[index - 1]], returns[calendar[index]])
        for index in range(2, len(calendar))
        if calendar[index - 1] in returns and calendar[index] in returns
    ]
    if len(lag_pairs) < 2:
        return None
    lagged = np.asarray(lag_pairs, dtype=float)
    if np.std(lagged[:, 0]) == 0 or np.std(lagged[:, 1]) == 0:
        return None
    result = {
        "log_dollar_volume": math.log(float(np.mean([bar.close * bar.volume for bar in ordered]))),
        "log_realized_volatility": math.log(math.sqrt(252 * variance)),
        "log_intraday_range": math.log(
            max(_FLOOR, float(np.median([(bar.high - bar.low) / bar.close for bar in ordered])))
        ),
        "log_overnight_gap": math.log(
            max(
                _FLOOR,
                float(
                    np.mean(
                        [
                            abs(math.log(clean[current].open / clean[previous].close))
                            for previous, current in pairwise(calendar)
                            if previous in clean and current in clean
                        ]
                    )
                ),
            )
        ),
        "market_beta": float(np.cov(asset_aligned, market_aligned, ddof=1)[0, 1]) / market_variance,
        "return_autocorrelation": float(np.corrcoef(lagged[:, 0], lagged[:, 1])[0, 1]),
    }
    return result if all(math.isfinite(value) for value in result.values()) else None


def discover(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    calendar: Sequence[date],
    config: UniverseDiscoveryConfig,
) -> DiscoveryResult:
    """Screen, rank, describe and complete-linkage cluster one frozen window."""
    sessions = sorted(day for day in calendar if config.start <= day <= config.end)
    if not sessions:
        raise ValueError("discovery window contains no trading sessions")
    session_set = set(sessions)
    clean_by_symbol: dict[str, dict[date, Bar]] = {}
    exclusions: dict[str, str] = {}
    for symbol, bars in bars_by_symbol.items():
        clean, reason = _clean_bars(bars, session_set)
        if reason is not None:
            exclusions[symbol] = reason
        elif len(clean) < config.min_observations:
            exclusions[symbol] = "insufficient_observations"
        elif len(clean) / len(sessions) < config.min_coverage:
            exclusions[symbol] = "insufficient_coverage"
        else:
            clean_by_symbol[symbol] = clean

    benchmark = clean_by_symbol.get(config.benchmark_symbol)
    if benchmark is None:
        raise ValueError("benchmark has insufficient or invalid discovery history")
    benchmark_returns = _return_series(benchmark, sessions)
    descriptors: dict[str, dict[str, float]] = {}
    for symbol, clean in clean_by_symbol.items():
        values = _descriptors(clean, sessions, benchmark_returns)
        if values is None:
            exclusions[symbol] = "invalid_descriptors"
        else:
            descriptors[symbol] = values
    selected = sorted(
        descriptors,
        key=lambda symbol: (-descriptors[symbol]["log_dollar_volume"], symbol),
    )[: config.top_n]
    if len(selected) < 2:
        raise ValueError("fewer than two assets qualify for discovery")

    raw = np.asarray(
        [[descriptors[symbol][name] for name in DESCRIPTOR_NAMES] for symbol in selected]
    )
    medians = np.median(raw, axis=0)
    q25, q75 = np.percentile(raw, [25, 75], axis=0)
    iqrs = q75 - q25
    configured_weights = np.asarray(
        [config.feature_weights.get(name, 0.0) for name in DESCRIPTOR_NAMES]
    )
    active = (iqrs > 0) & (configured_weights > 0)
    if not active.any():
        raise ValueError("no positively weighted descriptor has nonzero IQR")
    standardized = np.zeros_like(raw)
    standardized[:, active] = (raw[:, active] - medians[active]) / iqrs[active]
    weights = configured_weights[active]
    differences = standardized[:, None, active] - standardized[None, :, active]
    distances = np.sqrt(np.sum(weights * differences**2, axis=2) / np.sum(weights))
    labels = fcluster(
        linkage(squareform(distances), method="complete"),
        config.distance_threshold,
        criterion="distance",
    )
    clusters: dict[int, list[str]] = {}
    for symbol, label in zip(selected, labels, strict=True):
        clusters.setdefault(int(label), []).append(symbol)
    ordered_clusters = sorted(
        (sorted(members) for members in clusters.values()), key=lambda members: tuple(members)
    )
    named: dict[str, list[str]] = {"all": sorted(selected)}
    undersized: list[list[str]] = []
    symbol_cluster: dict[str, str] = {}
    bucket_index = 1
    for members in ordered_clusters:
        if len(members) < config.min_bucket_size:
            name = "undersized"
            undersized.append(members)
        else:
            name = f"bucket-{bucket_index:03d}"
            named[name] = members
            bucket_index += 1
        symbol_cluster.update(dict.fromkeys(members, name))
    scaling = {
        name: {"median": float(medians[index]), "iqr": float(iqrs[index])}
        for index, name in enumerate(DESCRIPTOR_NAMES)
    }
    features = tuple(
        FeatureRecord(
            symbol=symbol,
            rank=index + 1,
            descriptors=descriptors[symbol],
            standardized={
                name: float(standardized[index, column])
                for column, name in enumerate(DESCRIPTOR_NAMES)
            },
            cluster=symbol_cluster[symbol],
        )
        for index, symbol in enumerate(selected)
    )
    return DiscoveryResult(
        features,
        named,
        undersized,
        scaling,
        exclusions,
        tuple(name for index, name in enumerate(DESCRIPTOR_NAMES) if not active[index]),
        distances,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_snapshot(path: Path) -> UniverseSnapshot:
    return load_universe_snapshot(path)


def list_snapshots(root: Path) -> dict[str, Any]:
    """Small local catalogue for the TUI; invalid snapshots stay visible as warnings."""
    snapshots: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not root.is_dir():
        return {"snapshots": [], "warnings": [f"universe root does not exist: {root}"]}
    paths = (
        [root / "buckets.yaml"]
        if (root / "buckets.yaml").is_file()
        else sorted(root.glob("*/buckets.yaml"))
    )
    for path in paths:
        try:
            snapshot = load_universe_snapshot(path)
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        snapshots.append(
            {
                "path": str(path),
                "digest": snapshot.content_digest,
                "discovery_start": snapshot.discovery.start.isoformat(),
                "discovery_end": snapshot.discovery.end.isoformat(),
                "earliest_training_start": (
                    snapshot.discovery.end + timedelta(days=1)
                ).isoformat(),
                "buckets": {name: len(members) for name, members in snapshot.buckets.items()},
            }
        )
    return {"snapshots": snapshots, "warnings": warnings}


def publish_snapshot(
    config: UniverseDiscoveryConfig,
    result: DiscoveryResult,
    assets: Sequence[Mapping[str, str]],
    calendar: Sequence[date],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    reused: bool = False,
    asset_catalogue_retrieved_at: datetime | None = None,
) -> Path:
    """Write one immutable, checksummed snapshot using atomic directory rename."""
    root = config.output_root
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".universe.tmp-{uuid4().hex}"
    temporary.mkdir()
    try:
        (temporary / "assets.json").write_text(
            json.dumps(list(assets), indent=2, sort_keys=True) + "\n"
        )
        (temporary / "calendar.json").write_text(
            json.dumps([day.isoformat() for day in calendar], indent=2) + "\n"
        )
        rows = [
            {
                "symbol": symbol,
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for symbol, bars in sorted(bars_by_symbol.items())
            for bar in bars
        ]
        pd.DataFrame(
            rows,
            columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"],
        ).to_parquet(temporary / "bars.parquet", index=False)
        checksums = {
            name: _file_sha256(temporary / name)
            for name in ("assets.json", "calendar.json", "bars.parquet")
        }
        snapshot = UniverseSnapshot(
            created_at=datetime.now(UTC),
            discovery=config,
            asset_catalogue_retrieved_at=asset_catalogue_retrieved_at or datetime.now(UTC),
            asset_catalogue="reused" if reused else "current",
            buckets=result.buckets,
            undersized_clusters=result.undersized_clusters,
            scaling=result.scaling,
            input_checksums=checksums,
        )
        snapshot.content_digest = snapshot_digest(snapshot)
        (temporary / "buckets.yaml").write_text(
            yaml.safe_dump(snapshot.model_dump(mode="json"), sort_keys=False)
        )
        asset_lookup = {str(asset["symbol"]): asset for asset in assets}
        feature_rows = []
        for feature in result.features:
            asset = asset_lookup.get(feature.symbol, {})
            row: dict[str, object] = {
                "symbol": feature.symbol,
                "name": asset.get("name", ""),
                "exchange": asset.get("exchange", ""),
                "rank": feature.rank,
                "cluster": feature.cluster,
            }
            row.update(feature.descriptors)
            row.update(
                {f"standardized_{key}": value for key, value in feature.standardized.items()}
            )
            feature_rows.append(row)
        pd.DataFrame(feature_rows).to_csv(temporary / "features.csv", index=False)
        positions = {feature.symbol: index for index, feature in enumerate(result.features)}
        cluster_names = [name for name in result.buckets if name != "all"]
        bucket_diagnostics: dict[str, dict[str, object]] = {}
        for name in cluster_names:
            indexes = [positions[symbol] for symbol in result.buckets[name]]
            within = result.distances[np.ix_(indexes, indexes)]
            means = np.mean(within, axis=1)
            outside = [
                positions[symbol]
                for other in cluster_names
                if other != name
                for symbol in result.buckets[other]
            ]
            bucket_diagnostics[name] = {
                "representative": result.buckets[name][int(np.argmin(means))],
                "max_within_distance": float(np.max(within)),
                "nearest_bucket_distance": (
                    float(np.min(result.distances[np.ix_(indexes, outside)])) if outside else None
                ),
            }
        report = {
            "survivorship_warning": (
                "Candidates come from Alpaca's current asset catalogue; historical results are "
                "survivorship-biased and are not a point-in-time market-wide top 500."
            ),
            "feed_warning": (
                "IEX is exchange-only volume, not consolidated market volume."
                if config.feed == "iex"
                else None
            ),
            "selected_assets": len(result.buckets["all"]),
            "requested_assets": config.top_n,
            "configured_exclusions": config.exclude_symbols,
            "usable_bucket_count": len(cluster_names),
            "bucket_sizes": {key: len(value) for key, value in result.buckets.items()},
            "bucket_diagnostics": bucket_diagnostics,
            "undersized_clusters": result.undersized_clusters,
            "exclusions": result.exclusions,
            "dropped_descriptors": list(result.dropped_descriptors),
        }
        (temporary / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        target = root / f"{snapshot.created_at:%Y%m%dT%H%M%SZ}-{snapshot.content_digest[:12]}"
        if target.exists():
            raise FileExistsError(f"immutable universe snapshot already exists: {target}")
        os.replace(temporary, target)
        return target
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load_reuse_inputs(
    path: Path, config: UniverseDiscoveryConfig
) -> tuple[list[dict[str, str]], list[date], dict[str, list[Bar]], datetime]:
    """Load and checksum frozen source inputs for network-free reclustering."""
    directory = path if path.is_dir() else path.parent
    snapshot = load_snapshot(directory)
    previous = snapshot.discovery
    for field in ("start", "end", "feed", "benchmark_symbol"):
        if getattr(previous, field) != getattr(config, field):
            raise ValueError(f"reused data has incompatible {field}")
    for name, expected in snapshot.input_checksums.items():
        source = directory / name
        if not source.is_file() or _file_sha256(source) != expected:
            raise ValueError(f"reused input checksum mismatch: {source}")
    assets = json.loads((directory / "assets.json").read_text())
    calendar = [
        date.fromisoformat(value) for value in json.loads((directory / "calendar.json").read_text())
    ]
    frame = pd.read_parquet(directory / "bars.parquet")
    bars: dict[str, list[Bar]] = {}
    rows = cast(list[dict[str, Any]], frame.to_dict(orient="records"))
    for row in rows:
        value = row["timestamp"]
        timestamp = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
        if not isinstance(timestamp, datetime):
            raise ValueError("reused bar timestamp is not a datetime")
        symbol = str(row["symbol"])
        bars.setdefault(symbol, []).append(
            Bar(
                symbol=symbol,
                timestamp=timestamp,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )
    return assets, calendar, bars, snapshot.asset_catalogue_retrieved_at
