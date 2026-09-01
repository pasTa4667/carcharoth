"""Lazy entry points keep native XGBoost out of config-only CLI commands."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from carcharoth.config.app_config import ModelConfig
    from carcharoth.config.run_config import RunConfig
    from carcharoth.interfaces.optimization import BarsFetcher
    from carcharoth.modeling.signal_generator import XGBoostSignalGenerator


def build_signal_generator(
    config: ModelConfig, evaluation_start: date | None = None
) -> XGBoostSignalGenerator:
    from carcharoth.modeling.signal_generator import build_signal_generator as build

    return build(config, evaluation_start)


def train_and_publish(config: RunConfig, config_hash: str, fetch_bars: BarsFetcher) -> Path:
    from carcharoth.modeling.training import train_and_publish as train

    return train(config, config_hash, fetch_bars)


__all__ = ["build_signal_generator", "train_and_publish"]
