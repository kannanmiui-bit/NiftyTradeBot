"""
utils/logger.py — Centralized logging setup.
All modules should do:  from utils.logger import get_logger; logger = get_logger(__name__)
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_initialized = False


def setup_logger(name: str = "nifty_bot") -> logging.Logger:
    global _initialized
    logger = logging.getLogger(name)

    if _initialized:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console — INFO and above
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File — DEBUG and above, rotating 10 MB × 5 backups
    log_path = os.path.join(LOG_DIR, "bot.log")
    fh = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=5)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _initialized = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the root 'nifty_bot' namespace."""
    setup_logger()
    return logging.getLogger(f"nifty_bot.{name}")
