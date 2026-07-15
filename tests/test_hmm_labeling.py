"""State -> regime labeling from emission means (pure, no hmmlearn)."""

import logging

import numpy as np
import pytest

from carcharoth.regime.hmm.labeling import label_states
from carcharoth.regime.models import Regime

# columns: [log_return, volatility, ema_distance, adx]


def test_clear_means_label_all_four_regimes() -> None:
    means = np.array(
        [
            [0.5, 0.0, 0.8, 1.0],  # strong up trend
            [-0.5, 0.1, -0.9, 1.0],  # strong down trend
            [0.0, -0.5, 0.0, -1.0],  # calm, directionless
            [0.0, 2.0, 0.1, 0.5],  # highest volatility
        ]
    )
    assert label_states(means) == {
        0: Regime.TRENDING_UP,
        1: Regime.TRENDING_DOWN,
        2: Regime.RANGE_BOUND,
        3: Regime.HIGH_VOLATILITY,
    }


def test_two_trending_up_states_label_deterministically() -> None:
    means = np.array(
        [
            [0.6, 0.0, 0.7, 1.0],  # trend score 1.3
            [0.5, 0.0, 0.6, 1.0],  # trend score 1.1 -> the weaker becomes range
            [0.0, 2.0, 0.0, 0.0],
            [-0.1, 0.0, -0.2, 0.0],  # trend score -0.3
        ]
    )
    assert label_states(means) == {
        0: Regime.TRENDING_UP,
        1: Regime.RANGE_BOUND,
        2: Regime.HIGH_VOLATILITY,
        3: Regime.TRENDING_DOWN,
    }


def test_identical_means_still_yield_a_valid_permutation_and_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    means = np.zeros((4, 4))
    with caplog.at_level(logging.WARNING):
        labels = label_states(means)
    assert sorted(labels) == [0, 1, 2, 3]
    assert set(labels.values()) == {
        Regime.TRENDING_UP,
        Regime.TRENDING_DOWN,
        Regime.RANGE_BOUND,
        Regime.HIGH_VOLATILITY,
    }
    assert "barely separated" in caplog.text


def test_volatility_tie_breaks_to_lowest_state_index() -> None:
    means = np.array(
        [
            [0.0, 2.0, 0.5, 0.0],
            [0.0, 2.0, -0.5, 0.0],
            [0.5, 0.0, 0.5, 0.0],
            [-0.5, 0.0, -0.5, 0.0],
        ]
    )
    labels = label_states(means)
    assert labels[0] is Regime.HIGH_VOLATILITY


def test_extra_states_become_range_bound() -> None:
    means = np.array(
        [
            [0.5, 0.0, 0.5, 0.0],
            [-0.5, 0.0, -0.5, 0.0],
            [0.0, 2.0, 0.0, 0.0],
            [0.1, 0.0, 0.0, 0.0],
            [-0.1, 0.0, 0.0, 0.0],
        ]
    )
    labels = label_states(means)
    assert labels[3] is Regime.RANGE_BOUND
    assert labels[4] is Regime.RANGE_BOUND


def test_rejects_fewer_than_four_states() -> None:
    with pytest.raises(ValueError, match="at least 4 states"):
        label_states(np.zeros((3, 4)))
