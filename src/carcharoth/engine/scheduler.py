"""Minute-tick scheduler with market-hours gating and graceful shutdown."""

import logging
import threading
import time

from carcharoth.engine.engine import TradingEngine
from carcharoth.interfaces.clock import MarketClock

logger = logging.getLogger(__name__)

# Re-check the market clock at least this often while closed, so holidays
# and early closes reported by the broker are picked up.
_MAX_CLOSED_WAIT_SECONDS = 300.0


class Scheduler:
    def __init__(
        self,
        engine: TradingEngine,
        clock: MarketClock,
        interval_seconds: int = 60,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._interval = interval_seconds
        self._stop_event = threading.Event()

    def stop(self) -> None:
        logger.info("shutdown requested")
        self._stop_event.set()

    def run_forever(self) -> None:
        logger.info("scheduler started (interval=%ss)", self._interval)
        while not self._stop_event.is_set():
            try:
                if self._clock.is_open():
                    self._run_tick()
                    wait = self._seconds_to_next_interval()
                else:
                    until_open = self._clock.seconds_until_open()
                    wait = min(until_open, _MAX_CLOSED_WAIT_SECONDS)
                    logger.info(
                        "market closed, next open in %.0fs; sleeping %.0fs", until_open, wait
                    )
            except Exception:
                logger.exception("scheduler iteration failed; retrying next interval")
                wait = float(self._interval)
            self._stop_event.wait(wait)
        logger.info("scheduler stopped")

    def _run_tick(self) -> None:
        started = time.monotonic()
        self._engine.tick()
        logger.info("tick completed in %.2fs", time.monotonic() - started)

    def _seconds_to_next_interval(self) -> float:
        return max(1.0, self._interval - (time.time() % self._interval))
