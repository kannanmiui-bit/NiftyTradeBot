"""strategy/volume_strategy.py — Volume confirmation scorer.

High volume in the direction of price movement confirms the signal.
Requires VOL_MA column pre-computed by IndicatorEngine.
"""

import pandas as pd
from strategy.base_strategy import BaseStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


class VolumeStrategy(BaseStrategy):
    name = "volume"

    def __init__(self, period: int = 20, multiplier: float = 1.2):
        self.period = period
        self.multiplier = multiplier  # current volume must exceed avg by this factor

    def score(self, df: pd.DataFrame) -> int:
        if "VOL_MA" not in df.columns or "volume" not in df.columns:
            logger.debug("VOL_MA column missing — scoring 0.")
            return 0

        valid = df.dropna(subset=["VOL_MA", "volume", "open", "close"])
        if valid.empty:
            return 0

        last = valid.iloc[-1]
        current_vol = last["volume"]
        avg_vol = last["VOL_MA"]

        if avg_vol == 0:
            return 0

        # High volume confirms the direction of the price move on this candle
        if current_vol >= avg_vol * self.multiplier:
            if last["close"] >= last["open"]:
                return 1   # high volume bullish candle → confirms CE
            else:
                return -1  # high volume bearish candle → confirms PE

        return 0
