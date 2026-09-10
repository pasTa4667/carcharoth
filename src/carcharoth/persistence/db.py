from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def build_engine(
    database_url: str, pool_size: int | None = None, max_overflow: int | None = None
) -> Engine:
    """Pool sizes default to SQLAlchemy's; parallel optimize workers pass
    small explicit pools so N workers stay well under Postgres's limit."""
    kwargs: dict[str, int] = {}
    if pool_size is not None:
        kwargs["pool_size"] = pool_size
    if max_overflow is not None:
        kwargs["max_overflow"] = max_overflow
    return create_engine(database_url, pool_pre_ping=True, **kwargs)


def build_performance_engine(database_url: str) -> Engine:
    """Independent, bounded telemetry pool; never alters trading transactions.

    PostgreSQL timeouts bound server work, not every possible network stall.
    The monitor disables periodic writes after the first slow/failing attempt.
    """
    return create_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.1,
        connect_args={
            "connect_timeout": 2,
            "options": "-c statement_timeout=100 -c lock_timeout=50",
            "keepalives": 1,
            "keepalives_idle": 2,
            "keepalives_interval": 1,
            "keepalives_count": 2,
        },
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
