from unittest.mock import MagicMock

import pytest

from carcharoth.performance import Monitor, stage
from carcharoth.persistence.repositories import SqlAlchemyPerformanceRepository


def test_publish_is_one_transaction_with_cumulative_upserts() -> None:
    factory = MagicMock()
    session = factory.begin.return_value.__enter__.return_value
    with Monitor().execution() as monitor, stage("simulation"):
        pass
    repository = SqlAlchemyPerformanceRepository(factory)
    repository.publish(monitor.snapshot())
    factory.begin.assert_called_once()
    assert session.execute.call_count == 2
    statements = [str(call.args[0]) for call in session.execute.call_args_list]
    assert all("ON CONFLICT" in statement for statement in statements)
    assert "backtest_performance_stages" in statements[1]


def test_stage_failure_exits_same_transaction_with_exception() -> None:
    factory = MagicMock()
    session = factory.begin.return_value.__enter__.return_value
    session.execute.side_effect = [None, OSError("stage write failed")]
    with Monitor().execution() as monitor, stage("simulation"):
        pass
    with pytest.raises(OSError):
        SqlAlchemyPerformanceRepository(factory).publish(monitor.snapshot())
    assert factory.begin.return_value.__exit__.call_args.args[0] is OSError
