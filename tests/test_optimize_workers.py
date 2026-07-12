"""Trial-budget splitting for parallel optimize workers."""

from carcharoth.main import _split_trials


def test_split_evenly() -> None:
    assert _split_trials(8, 4) == [2, 2, 2, 2]


def test_remainder_goes_to_earlier_workers() -> None:
    assert _split_trials(10, 4) == [3, 3, 2, 2]


def test_more_workers_than_trials_yields_zero_shares() -> None:
    assert _split_trials(2, 4) == [1, 1, 0, 0]


def test_sum_always_matches_total() -> None:
    for total in range(1, 30):
        for workers in range(1, 8):
            assert sum(_split_trials(total, workers)) == total
