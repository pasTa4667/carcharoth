"""Provider-agnostic error types raised by service implementations."""


class ServiceError(Exception):
    """Base for all service-layer failures."""


class MarketDataError(ServiceError):
    """Market data could not be fetched; the tick should be aborted."""


class AccountError(ServiceError):
    """Account state could not be fetched."""


class BrokerError(ServiceError):
    """Order submission or lookup failed."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
