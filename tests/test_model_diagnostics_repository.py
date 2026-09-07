from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from carcharoth.persistence.orm import ModelDiagnosticsRow
from carcharoth.persistence.repositories import SqlAlchemyModelDiagnosticsRepository


def repository_with_session() -> tuple[SqlAlchemyModelDiagnosticsRepository, MagicMock]:
    session_factory = MagicMock()
    session = session_factory.begin.return_value.__enter__.return_value
    return SqlAlchemyModelDiagnosticsRepository(session_factory), session


def test_save_inserts_new_model_diagnostics() -> None:
    repository, session = repository_with_session()
    session.get.return_value = None
    created_at = datetime(2024, 1, 4, tzinfo=UTC)

    repository.save(
        "model-1",
        "regression",
        date(2024, 1, 1),
        date(2024, 1, 3),
        created_at,
        {"diagnostics": {"schema_version": 1}},
    )

    session.get.assert_called_once_with(ModelDiagnosticsRow, "model-1")
    saved = session.add.call_args.args[0]
    assert saved.model_id == "model-1"
    assert saved.created_at == created_at
    assert saved.report["diagnostics"]["schema_version"] == 1


def test_save_updates_existing_model_diagnostics_idempotently() -> None:
    repository, session = repository_with_session()
    existing = SimpleNamespace(
        task="old",
        validation_start=None,
        validation_end=None,
        created_at=None,
        report={},
    )
    session.get.return_value = existing
    report = {"diagnostics": {"schema_version": 1}}

    repository.save(
        "model-1",
        "regression",
        date(2024, 1, 1),
        date(2024, 1, 3),
        datetime(2024, 1, 4, tzinfo=UTC),
        report,
    )

    session.add.assert_not_called()
    assert existing.task == "regression"
    assert existing.validation_end == date(2024, 1, 3)
    assert existing.report == report
