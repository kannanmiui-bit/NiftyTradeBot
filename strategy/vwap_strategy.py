"""strategy/vwap_strategy.py — VWAP deviation scorer."""

import pandas as pd
from strategy.base_strategy import BaseStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


class VWAPStrategy(BaseStrategy):
    name = "vwap"

    def __init__(self, band_pct: float = 0.003):
        self.band_pct = band_pct  # e.g., 0.003 = 0.3% deviation

    def score(self, df: pd.DataFrame) -> int:
        if "VWAP_D" not in df.columns:
            logger.debug("VWAP column missing — scoring 0.")
            return 0

        valid = df.dropna(subset=["VWAP_D", "close"])
        if valid.empty:
            return 0

        last = valid.iloc[-1]
        close = last["close"]
        vwap = last["VWAP_D"]

        if vwap == 0:
            return 0

        deviation = (close - vwap) / vwap

        if deviation > self.band_pct:
            return 1   # price well above VWAP → bullish
        elif deviation < -self.band_pct:
            return -1  # price well below VWAP → bearish
        return 0
