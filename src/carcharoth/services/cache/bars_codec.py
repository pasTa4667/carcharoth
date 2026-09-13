"""Private monthly Arrow IPC codec. No provider or pickle objects cross this boundary."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from carcharoth.domain.models import Bar

Interval = tuple[datetime, datetime]
_FIELDS = ("open", "high", "low", "close", "volume")
_SCHEMA = pa.schema(
    [
        pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
        *(pa.field(name, pa.float64(), nullable=False) for name in _FIELDS),
    ]
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def micros(value: datetime) -> int:
    delta = value - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def union(intervals: Sequence[Interval]) -> tuple[Interval, ...]:
    result: list[Interval] = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return tuple(result)


@dataclass(frozen=True)
class Chunk:
    timestamps: npt.NDArray[np.int64]
    values: npt.NDArray[np.float64]
    coverage: tuple[Interval, ...]

    @classmethod
    def empty(cls) -> "Chunk":
        return cls(np.empty(0, dtype=np.int64), np.empty((0, 5)), ())

    @property
    def nbytes(self) -> int:
        return self.timestamps.nbytes + self.values.nbytes

    def bars(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        left, right = self.timestamps.searchsorted([micros(start), micros(end)])
        # Only requested rows become Python/domain objects.
        dates = pa.array(self.timestamps[left:right], type=pa.timestamp("us", tz="UTC")).to_pylist()
        return [
            Bar(symbol=symbol, timestamp=ts, **dict(zip(_FIELDS, map(float, row), strict=True)))
            for ts, row in zip(dates, self.values[left:right], strict=True)
        ]

    def before(self, end: datetime) -> "Chunk":
        right = self.timestamps.searchsorted(micros(end))
        return Chunk(
            self.timestamps[:right],
            self.values[:right],
            union([(a, min(b, end)) for a, b in self.coverage]),
        )

    def merge(self, fresh: Sequence[Bar], coverage: Interval) -> "Chunk":
        # Stable sort + last occurrence makes the last fresh value authoritative.
        timestamps = np.concatenate(
            (self.timestamps, np.array([micros(bar.timestamp) for bar in fresh], dtype=np.int64))
        )
        values = np.concatenate(
            (
                self.values,
                np.array(
                    [[getattr(bar, field) for field in _FIELDS] for bar in fresh], dtype=np.float64
                ).reshape(-1, 5),
            )
        )
        order = np.argsort(timestamps, kind="stable")
        timestamps, values = timestamps[order], values[order]
        keep = np.r_[timestamps[1:] != timestamps[:-1], True] if len(timestamps) else []
        return Chunk(timestamps[keep], values[keep], union([*self.coverage, coverage]))


def encode(chunk: Chunk, symbol: str, timeframe: str, month: str) -> bytes:
    metadata = {
        b"carcharoth": json.dumps(
            {
                "version": 2,
                "symbol": symbol,
                "timeframe": timeframe,
                "month": month,
                "coverage": [[a.isoformat(), b.isoformat()] for a, b in chunk.coverage],
            },
            sort_keys=True,
        ).encode()
    }
    table = pa.Table.from_arrays(
        [
            pa.array(chunk.timestamps, type=_SCHEMA.field(0).type),
            *(pa.array(chunk.values[:, i]) for i in range(5)),
        ],
        schema=_SCHEMA.with_metadata(metadata),
    )
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(
        sink, table.schema, options=pa.ipc.IpcWriteOptions(compression="zstd")
    ) as writer:
        writer.write_table(table)
    return bytes(sink.getvalue())


def decode(
    payload: bytes,
    symbol: str,
    timeframe: str,
    month: str,
    month_start: datetime,
    month_end: datetime,
) -> Chunk:
    table = pa.ipc.open_file(pa.BufferReader(payload)).read_all()
    if not table.schema.equals(_SCHEMA, check_metadata=False):
        raise ValueError("invalid bars schema")
    metadata = json.loads((table.schema.metadata or {})[b"carcharoth"])
    if any(
        metadata.get(key) != value
        for key, value in {
            "version": 2,
            "symbol": symbol,
            "timeframe": timeframe,
            "month": month,
        }.items()
    ):
        raise ValueError("invalid bars identity")
    coverage = tuple(
        (datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in metadata["coverage"]
    )
    if any(
        a.utcoffset() != UTC.utcoffset(a)
        or b.utcoffset() != UTC.utcoffset(b)
        or not month_start <= a < b <= month_end
        for a, b in coverage
    ):
        raise ValueError("invalid bars coverage")
    if coverage != union(coverage):
        raise ValueError("noncanonical bars coverage")
    if any(column.null_count for column in table.columns):
        raise ValueError("null bars values")
    timestamps = table.column(0).cast(pa.int64()).to_numpy().copy()
    values = np.column_stack([table.column(name).to_numpy() for name in _FIELDS])
    if len(timestamps) and (
        np.any(timestamps[1:] <= timestamps[:-1])
        or timestamps[0] < micros(month_start)
        or timestamps[-1] >= micros(month_end)
    ):
        raise ValueError("unsorted, duplicate, or out-of-month bars")
    covered = np.zeros(len(timestamps), dtype=bool)
    for a, b in coverage:
        covered |= (timestamps >= micros(a)) & (timestamps < micros(b))
    if not covered.all():
        raise ValueError("bars outside declared coverage")
    return Chunk(timestamps, values, coverage)
