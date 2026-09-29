"""NYSE regular-session helpers shared by features and replay."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from carcharoth.domain.models import Bar

MARKET_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
REGULAR_SESSION_MINUTES = 390


@dataclass(frozen=True, slots=True)
class TradingSession:
    open: datetime
    close: datetime

    def __post_init__(self) -> None:
        if any(t.tzinfo is None or t.utcoffset() is None for t in (self.open, self.close)):
            raise ValueError("trading sessions require aware timestamps")
        if self.close <= self.open:
            raise ValueError("session close must follow open")


CalendarFetcher = Callable[[date, date], Sequence[TradingSession]]


def scheduled_bars(
    sessions: Sequence[TradingSession], timeframe_minutes: int
) -> dict[datetime, date]:
    """Regular-session interval starts, including a partial final interval.

    Alpaca minute aggregates align to clock intervals from local midnight,
    not a fresh interval grid at the exchange open.
    """
    step = timedelta(minutes=timeframe_minutes)
    if timeframe_minutes <= 0:
        raise ValueError("timeframe must be positive")
    result: dict[datetime, date] = {}
    for session in sessions:
        opened = session.open.astimezone(MARKET_TZ)
        midnight = opened.replace(hour=0, minute=0, second=0, microsecond=0)
        slots, remainder = divmod(opened - midnight, step)
        cursor = midnight + (slots + bool(remainder)) * step
        while cursor < session.close:
            result[cursor] = opened.date()
            cursor += step
    return result


def is_regular_session(ts: datetime) -> bool:
    local = ts.astimezone(MARKET_TZ).time()
    return SESSION_OPEN <= local < SESSION_CLOSE


def regular_session_bars(bars: Sequence[Bar]) -> list[Bar]:
    return [bar for bar in bars if is_regular_session(bar.timestamp)]


def current_session_bars(bars: Sequence[Bar]) -> list[Bar]:
    """Regular-session bars from the latest bar's New York date."""
    if not bars:
        return []
    session_date = bars[-1].timestamp.astimezone(MARKET_TZ).date()
    return [
        bar
        for bar in bars
        if is_regular_session(bar.timestamp)
        and bar.timestamp.astimezone(MARKET_TZ).date() == session_date
    ]


def minutes_until_close(ts: datetime) -> float:
    local = ts.astimezone(MARKET_TZ)
    close = local.replace(
        hour=SESSION_CLOSE.hour, minute=SESSION_CLOSE.minute, second=0, microsecond=0
    )
    return (close - local).total_seconds() / 60.0


def minutes_since_open(ts: datetime) -> float:
    local = ts.astimezone(MARKET_TZ)
    open_ = local.replace(
        hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0
    )
    return (local - open_).total_seconds() / 60.0
