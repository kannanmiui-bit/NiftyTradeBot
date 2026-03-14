"""strategy/rsi_strategy.py — RSI momentum scorer."""

import pandas as pd
from strategy.base_strategy import BaseStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


class RSIStrategy(BaseStrategy):
    name = "rsi"

    def __init__(self, period: int = 14, overbought: int = 60, oversold: int = 40):
        self.period = period
        self.overbought = overbought
        self.oversold = oversold

    def score(self, df: pd.DataFrame) -> int:
        col = f"RSI_{self.period}"
        if col not in df.columns or df[col].isna().all():
            logger.debug("RSI column missing — scoring 0.")
            return 0

        rsi_series = df[col].dropna()
        if len(rsi_series) < 2:
            return 0

        rsi = rsi_series.iloc[-1]
        rsi_prev = rsi_series.iloc[-2]
        rising = rsi > rsi_prev

        if rsi > self.overbought and rising:
            # Strong upward momentum
            return 1
        elif rsi < self.oversold and not rising:
            # Downward momentum / weak market
            return -1
        return 0
