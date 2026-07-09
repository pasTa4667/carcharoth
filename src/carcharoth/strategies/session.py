"""NYSE regular-session helpers shared by intraday strategies.

Strategies are pure and cannot query the broker clock, so the regular NYSE
hours (09:30-16:00 America/New_York) live here as constants. Early-close
days are not modelled: on those days the end-of-day checks never fire and a
position is held until the next regular exit signal.
"""

from collections.abc import Sequence
from datetime import datetime, time
from zoneinfo import ZoneInfo

from carcharoth.domain.models import Bar

MARKET_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


def session_bars(bars: Sequence[Bar]) -> list[Bar]:
    """Bars belonging to the latest bar's regular session: same market-timezone
    date, at or after the session open (pre-market bars are excluded)."""
    if not bars:
        return []
    session_date = bars[-1].timestamp.astimezone(MARKET_TZ).date()
    result: list[Bar] = []
    for bar in bars:
        local = bar.timestamp.astimezone(MARKET_TZ)
        if local.date() == session_date and local.time() >= SESSION_OPEN:
            result.append(bar)
    return result


def minutes_until_close(ts: datetime) -> float:
    """Minutes from ts to the regular session close on ts's market-timezone
    date; negative once the close has passed."""
    local = ts.astimezone(MARKET_TZ)
    close = local.replace(
        hour=SESSION_CLOSE.hour, minute=SESSION_CLOSE.minute, second=0, microsecond=0
    )
    return (close - local).total_seconds() / 60.0
