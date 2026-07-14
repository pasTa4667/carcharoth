"""StrategyProvider implementations: fixed single-strategy and regime-driven.

RegimeStrategyProvider is where regime detection meets trading: it
periodically re-assesses each symbol's regime, persists the evaluation, and
switches the symbol's strategy hold-until-flat — an open position stays with
the strategy that entered it (whose exit logic understands it); the new
regime's strategy takes over once the symbol is flat.
"""

import logging
from collections.abc import Mapping
from datetime import datetime

from carcharoth.domain.models import Bar, BarSpec, Position
from carcharoth.interfaces.strategy import Strategy
from carcharoth.interfaces.strategy_provider import StrategyProvider
from carcharoth.persistence.repositories import (
    RegimeEvaluationRepository,
    StrategyAssignmentRepository,
)
from carcharoth.regime.detector import RegimeDetector
from carcharoth.regime.models import Regime, StrategyAssignment

logger = logging.getLogger(__name__)


class SingleStrategyProvider(StrategyProvider):
    """Every symbol trades the one configured strategy (legacy mode)."""

    def __init__(self, strategy: Strategy) -> None:
        self._strategy = strategy

    def required_bars(self) -> BarSpec:
        return self._strategy.required_bars()

    def resolve(
        self, symbol: str, bars: list[Bar], position: Position | None, as_of: datetime
    ) -> Strategy | None:
        return self._strategy


class RegimeStrategyProvider(StrategyProvider):
    def __init__(
        self,
        detector: RegimeDetector,
        strategies: Mapping[Regime, Strategy],
        evaluations_repo: RegimeEvaluationRepository,
        assignments_repo: StrategyAssignmentRepository,
        evaluate_every_ticks: int = 5,
        default_regime: Regime | None = None,
    ) -> None:
        if not strategies:
            raise ValueError("at least one regime -> strategy mapping is required")
        if default_regime is not None and default_regime not in strategies:
            raise ValueError(f"default regime {default_regime!r} has no mapped strategy")
        if evaluate_every_ticks < 1:
            raise ValueError("evaluate_every_ticks must be >= 1")

        timeframes = {s.required_bars().timeframe for s in strategies.values()}
        if len(timeframes) > 1:
            raise ValueError(
                "regime-mapped strategies must share one timeframe, got: "
                + ", ".join(f"{s.name}={s.required_bars().timeframe}" for s in strategies.values())
            )

        self._detector = detector
        self._strategies = dict(strategies)
        self._by_name = {s.name: s for s in strategies.values()}
        self._evaluations_repo = evaluations_repo
        self._assignments_repo = assignments_repo
        self._evaluate_every_ticks = evaluate_every_ticks
        self._default_regime = default_regime
        self._spec = BarSpec(
            timeframes.pop(),
            max(
                detector.required_lookback(),
                *(s.required_bars().lookback for s in strategies.values()),
            ),
        )

        self._assignments: dict[str, StrategyAssignment] = {}
        self._latest_regime: dict[str, Regime] = {}
        for symbol, assignment in self._assignments_repo.load_current().items():
            if assignment.strategy not in self._by_name:
                logger.warning(
                    "%s: dropping persisted assignment to unmapped strategy %r",
                    symbol,
                    assignment.strategy,
                )
                continue
            self._assignments[symbol] = assignment
            self._latest_regime[symbol] = assignment.regime
        self._ticks: dict[str, int] = {}

    def required_bars(self) -> BarSpec:
        return self._spec

    def resolve(
        self, symbol: str, bars: list[Bar], position: Position | None, as_of: datetime
    ) -> Strategy | None:
        tick = self._ticks.get(symbol, 0)
        self._ticks[symbol] = tick + 1
        if tick % self._evaluate_every_ticks == 0:
            self._assess(symbol, bars, as_of)

        detected = self._latest_regime.get(symbol)
        desired_regime = detected if detected is not None else self._default_regime
        current = self._assignments.get(symbol)

        # No regime detected yet (warm-up) or regime has no mapped strategy
        if desired_regime is None or desired_regime not in self._strategies:
            if current is not None:
                # hold-until-flat: keep the entering strategy managing the open position
                return self._by_name[current.strategy]
            logger.debug("%s: no regime strategy available, skipping tick", symbol)
            return None

        desired = self._strategies[desired_regime]
        if current is None:
            current = self._assign(symbol, desired_regime, as_of)
        elif current.strategy != desired.name and position is None:
            # hold-until-flat: with a position open the entering strategy
            # keeps managing it; _latest_regime is the pending target
            logger.info(
                "%s: regime switch %s -> %s, strategy %s -> %s",
                symbol,
                current.regime.value,
                desired_regime.value,
                current.strategy,
                desired.name,
            )
            current = self._assign(symbol, desired_regime, as_of)
        return self._by_name[current.strategy]

    def _assess(self, symbol: str, bars: list[Bar], as_of: datetime) -> None:
        assessment = self._detector.assess(symbol, bars)
        if assessment is None:
            logger.debug("%s: regime detector warming up (%d bars)", symbol, len(bars))
            return
        weights = {
            e.feature: weight
            for e in assessment.evidence
            if (weight := self._detector.weight_of(e.feature)) is not None
        }
        self._evaluations_repo.save(assessment, weights, as_of)
        self._latest_regime[symbol] = assessment.regime

    def _assign(self, symbol: str, regime: Regime, as_of: datetime) -> StrategyAssignment:
        assignment = StrategyAssignment(
            symbol=symbol,
            strategy=self._strategies[regime].name,
            regime=regime,
            since=as_of,
        )
        self._assignments_repo.save(assignment)
        self._assignments[symbol] = assignment
        return assignment
