from datetime import UTC, datetime
from uuid import uuid4

from carcharoth.domain.models import ExposureIntent, TargetExposure
from carcharoth.persistence.buffered import BufferedModelDecisionRepository, WriteBuffer
from carcharoth.persistence.orm import ModelDecisionRow


def test_model_decision_is_buffered_with_prediction_metadata() -> None:
    flushed = []
    buffer = WriteBuffer(lambda rows: flushed.append(rows), max_rows=10)
    repository = BufferedModelDecisionRepository(buffer, uuid4())
    intent = ExposureIntent(
        symbol="AAPL",
        target=TargetExposure.LONG,
        model_id="m1",
        schema_hash="s1",
        reason="edge",
    )
    repository.save(intent, None, datetime.now(UTC))
    assert flushed == []
    buffer.flush()
    row = flushed[0][ModelDecisionRow][0]
    assert row["model_id"] == "m1"
    assert row["target"] == "long"
    assert row["prediction"] == {}
    assert row["features"] == {}
