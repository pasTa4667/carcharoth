from datetime import UTC, datetime

import pytest

from carcharoth.strategies import session
from tests.factories import make_bars

# 2026-07-01 is an EDT date: ET = UTC-4, so the 09:30 open is 13:30 UTC and
# the 16:00 close is 20:00 UTC.


def test_session_bars_empty() -> None:
    assert session.session_bars([]) == []


def test_session_bars_drops_previous_day() -> None:
    previous_day = make_bars([100.0] * 3, start=datetime(2026, 6, 30, 15, 0, tzinfo=UTC))
    today = make_bars([100.0] * 3, start=datetime(2026, 7, 1, 15, 0, tzinfo=UTC))
    assert session.session_bars(previous_day + today) == today


def test_session_bars_drops_pre_market() -> None:
    # 13:10..13:30 UTC = 09:10..09:30 ET: only the 09:30 bar is in-session.
    bars = make_bars([100.0] * 5, start=datetime(2026, 7, 1, 13, 10, tzinfo=UTC))
    assert session.session_bars(bars) == bars[-1:]


def test_minutes_until_close() -> None:
    assert session.minutes_until_close(datetime(2026, 7, 1, 15, 0, tzinfo=UTC)) == pytest.approx(
        300.0
    )
    assert session.minutes_until_close(datetime(2026, 7, 1, 20, 30, tzinfo=UTC)) == pytest.approx(
        -30.0
    )
