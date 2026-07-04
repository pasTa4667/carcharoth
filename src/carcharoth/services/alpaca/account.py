from alpaca.trading.client import TradingClient
from alpaca.trading.models import Position, TradeAccount

from carcharoth.domain.errors import AccountError
from carcharoth.domain.models import AccountState
from carcharoth.interfaces.account import AccountService
from carcharoth.services.alpaca.mappers import to_account_state


class AlpacaAccountService(AccountService):
    def __init__(self, client: TradingClient) -> None:
        self._client = client

    def get_account_state(self) -> AccountState:
        try:
            account = self._client.get_account()
            positions = self._client.get_all_positions()
        except Exception as exc:
            raise AccountError(f"failed to fetch account state: {exc}") from exc
        assert isinstance(account, TradeAccount)
        return to_account_state(account, [p for p in positions if isinstance(p, Position)])
