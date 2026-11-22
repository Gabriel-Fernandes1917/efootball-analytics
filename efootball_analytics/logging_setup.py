import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")],
    )
