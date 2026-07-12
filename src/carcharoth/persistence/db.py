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


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
