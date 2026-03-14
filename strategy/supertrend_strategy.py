"""strategy/supertrend_strategy.py — Supertrend direction scorer."""

import pandas as pd
from strategy.base_strategy import BaseStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


class SupertrendStrategy(BaseStrategy):
    name = "supertrend"

    def __init__(self, period: int = 7, multiplier: float = 3.0):
        self.period = period
        self.multiplier = multiplier

    def score(self, df: pd.DataFrame) -> int:
        col = f"SUPERTd_{self.period}_{self.multiplier}"
        if col not in df.columns or df[col].isna().all():
            logger.debug("Supertrend column missing or all NaN — scoring 0.")
            return 0

        direction = df[col].dropna().iloc[-1]
        if direction == 1:
            return 1
        elif direction == -1:
            return -1
        return 0
