from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from carcharoth.persistence.repositories import SqlAlchemyEligibilityRepository
from tests.test_data_eligibility import report


def test_assessment_upsert_has_independent_identity_and_monotonic_updates():
    factory = MagicMock()
    repository = SqlAlchemyEligibilityRepository(factory)
    assessment = report()
    assessment.status = "rejected"
    repository.save(assessment)
    statement = factory.begin.return_value.__enter__.return_value.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (assessment_id) DO UPDATE" in sql
    assert "updated_at <= excluded.updated_at" in sql
    values = statement.compile().params
    assert values["model_id"] is None
    assert values["run_id"] is None
    assert values["report"]["status"] == "rejected"
