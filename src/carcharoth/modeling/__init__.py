"""Lazy entry points keep native XGBoost out of config-only CLI commands."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from carcharoth.config.app_config import ModelConfig
    from carcharoth.config.run_config import RunConfig
    from carcharoth.config.universe_config import UniverseMetadata
    from carcharoth.features.session import CalendarFetcher
    from carcharoth.interfaces.cache import ByteStore
    from carcharoth.interfaces.optimization import BarsFetcher
    from carcharoth.modeling.assessment_io import AssessmentPublisher
    from carcharoth.modeling.signal_generator import XGBoostSignalGenerator


def build_signal_generator(
    config: ModelConfig,
    evaluation_start: date | None = None,
    evaluation_universe: UniverseMetadata | None = None,
    hmm_store: ByteStore | None = None,
) -> XGBoostSignalGenerator:
    from carcharoth.modeling.signal_generator import build_signal_generator as build

    return build(config, evaluation_start, evaluation_universe, hmm_store)


def train_and_publish(
    config: RunConfig,
    config_hash: str,
    fetch_bars: BarsFetcher,
    fetch_calendar: CalendarFetcher,
    publish_assessment: AssessmentPublisher | None = None,
    *,
    loop_context: dict[str, object] | None = None,
    hmm_store: ByteStore | None = None,
) -> Path:
    from carcharoth.modeling.training import train_and_publish as train

    return train(
        config,
        config_hash,
        fetch_bars,
        fetch_calendar,
        publish_assessment,
        loop_context=loop_context,
        hmm_store=hmm_store,
    )


__all__ = ["build_signal_generator", "train_and_publish"]
