from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from carcharoth.domain.models import RunType
from carcharoth.persistence.repositories import SqlAlchemyRunRepository


@pytest.mark.parametrize(
    "context", [None, {"loop_id": str(uuid4()), "iteration": 1, "iterations": 2}]
)
def test_context_is_created_with_run_and_round_trips(context):
    factory = MagicMock()
    session = factory.begin.return_value.__enter__.return_value
    repository = SqlAlchemyRunRepository(factory)
    config = {"unmodified": True}
    run_id = repository.create(
        RunType.BACKTEST, config, ["SPY"], datetime.now(UTC), loop_context=context
    )
    row = session.add.call_args.args[0]
    assert row.run_id == run_id
    assert row.loop_context == context
    assert row.config == config
    factory.return_value.__enter__.return_value.get.return_value = row
    info = repository.get(run_id)
    assert info is not None
    assert info.loop_context == context
    assert info.config == config
    factory.begin.assert_called_once()
