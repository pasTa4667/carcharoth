"""Logging configuration: app.log, errors.log, trades.log, decisions.log.

Loggers:
- ``carcharoth.*``           -> app.log + console (INFO), errors.log (ERROR)
- ``carcharoth.trades``      -> additionally trades.log (order submits and fills)
- ``carcharoth.decisions``   -> decisions.log only (high volume, does not propagate)
"""

import logging.config
from pathlib import Path

TRADES_LOGGER = "carcharoth.trades"
DECISIONS_LOGGER = "carcharoth.decisions"

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    def file_handler(filename: str, level: str = "INFO") -> dict[str, object]:
        return {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_dir / filename),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "standard",
            "level": level,
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"standard": {"format": _FORMAT}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "level": "INFO",
                },
                "app_file": file_handler("app.log"),
                "errors_file": file_handler("errors.log", level="ERROR"),
                "trades_file": file_handler("trades.log"),
                "decisions_file": file_handler("decisions.log"),
            },
            "loggers": {
                "carcharoth": {
                    "level": "INFO",
                    "handlers": ["console", "app_file", "errors_file"],
                },
                TRADES_LOGGER: {
                    "level": "INFO",
                    "handlers": ["trades_file"],
                    "propagate": True,
                },
                DECISIONS_LOGGER: {
                    "level": "INFO",
                    "handlers": ["decisions_file"],
                    "propagate": False,
                },
            },
        }
    )
