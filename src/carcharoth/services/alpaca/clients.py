from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

from carcharoth.config.settings import Settings


def build_trading_client(settings: Settings) -> TradingClient:
    return TradingClient(
        api_key=settings.apca_api_key_id,
        secret_key=settings.apca_api_secret_key,
        paper=True,
    )


def build_data_client(settings: Settings) -> StockHistoricalDataClient:
    return StockHistoricalDataClient(
        api_key=settings.apca_api_key_id,
        secret_key=settings.apca_api_secret_key,
    )
