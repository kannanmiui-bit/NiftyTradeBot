"""strategy/ema_strategy.py — EMA crossover scorer."""

import pandas as pd
from strategy.base_strategy import BaseStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


class EMAStrategy(BaseStrategy):
    name = "ema"

    def __init__(self, fast: int = 9, slow: int = 21):
        self.fast = fast
        self.slow = slow

    def score(self, df: pd.DataFrame) -> int:
        fast_col = f"EMA_{self.fast}"
        slow_col = f"EMA_{self.slow}"

        if fast_col not in df.columns or slow_col not in df.columns:
            logger.debug("EMA columns missing — scoring 0.")
            return 0

        valid = df.dropna(subset=[fast_col, slow_col, "close"])
        if len(valid) < 2:
            return 0

        last = valid.iloc[-1]
        prev = valid.iloc[-2]

        ema_fast_now = last[fast_col]
        ema_slow_now = last[slow_col]
        ema_fast_prev = prev[fast_col]
        ema_slow_prev = prev[slow_col]
        close = last["close"]

        # Crossover: fast crossed above slow
        bullish_cross = ema_fast_prev <= ema_slow_prev and ema_fast_now > ema_slow_now
        # Crossover: fast crossed below slow
        bearish_cross = ema_fast_prev >= ema_slow_prev and ema_fast_now < ema_slow_now

        if bullish_cross:
            return 1
        elif bearish_cross:
            return -1

        # Trend continuation (no crossover this candle)
        if ema_fast_now > ema_slow_now and close > ema_fast_now:
            return 1
        elif ema_fast_now < ema_slow_now and close < ema_fast_now:
            return -1

        return 0
