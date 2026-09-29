"""Alpaca catalogue and calendar adapter for offline universe discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest, GetCalendarRequest

from carcharoth.domain.errors import MarketDataError
from carcharoth.features.session import MARKET_TZ, TradingSession

_ALLOWED_EXCHANGES = frozenset({"NYSE", "NASDAQ", "AMEX", "ARCA", "NYSEARCA"})


@dataclass(frozen=True, slots=True)
class AssetRecord:
    id: str
    symbol: str
    name: str
    exchange: str

    def payload(self) -> dict[str, str]:
        return asdict(self)


def map_assets(assets: list[Any], excluded: set[str]) -> list[AssetRecord]:
    records = []
    for asset in assets:
        symbol = str(asset.symbol).upper()
        exchange = str(getattr(asset.exchange, "value", asset.exchange))
        status = str(getattr(asset.status, "value", asset.status))
        asset_class = str(getattr(asset.asset_class, "value", asset.asset_class))
        if (
            status != AssetStatus.ACTIVE.value
            or asset_class != AssetClass.US_EQUITY.value
            or not asset.tradable
            or exchange not in _ALLOWED_EXCHANGES
            or symbol in excluded
        ):
            continue
        records.append(AssetRecord(str(asset.id), symbol, str(asset.name or ""), exchange))
    return sorted(records, key=lambda record: record.symbol)


def fetch_assets(client: TradingClient, excluded: set[str]) -> list[AssetRecord]:
    try:
        response = client.get_all_assets(
            GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
        )
    except Exception as exc:
        raise MarketDataError(f"failed to fetch Alpaca asset catalogue: {exc}") from exc
    if isinstance(response, dict):
        raise MarketDataError("Alpaca returned an unexpected raw asset response")
    return map_assets(response, excluded)


def fetch_calendar(client: TradingClient, start: date, end: date) -> list[date]:
    return [
        session.open.astimezone(MARKET_TZ).date() for session in fetch_sessions(client, start, end)
    ]


def fetch_sessions(client: TradingClient, start: date, end: date) -> list[TradingSession]:
    def aware(value: datetime) -> datetime:
        return value.replace(tzinfo=MARKET_TZ) if value.tzinfo is None else value

    try:
        response = client.get_calendar(GetCalendarRequest(start=start, end=end))
    except Exception as exc:
        raise MarketDataError(f"failed to fetch Alpaca trading calendar: {exc}") from exc
    if isinstance(response, dict):
        raise MarketDataError("Alpaca returned an unexpected raw calendar response")
    return [TradingSession(aware(entry.open), aware(entry.close)) for entry in response]
