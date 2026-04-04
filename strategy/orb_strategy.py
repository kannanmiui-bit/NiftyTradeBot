"""strategy/orb_strategy.py — Opening Range Breakout scorer."""

from datetime import datetime, time as dtime
import pandas as pd
import pytz

from strategy.base_strategy import BaseStrategy
from utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class ORBStrategy(BaseStrategy):
    name = "orb"

    def __init__(self, orb_end: str = "09:45"):
        h, m = map(int, orb_end.split(":"))
        self._orb_end = dtime(h, m)

    def score(self, df: pd.DataFrame) -> int:
        # Guard: ORB not yet established
        # Use last candle timestamp (works on any system timezone, not just IST)
        last_ts = df.index[-1]
        if hasattr(last_ts, 'tz') and last_ts.tz is not None:
            now_ist = last_ts.astimezone(IST)
        else:
            now_ist = IST.localize(last_ts)
        if now_ist.time() < self._orb_end:
            logger.debug("ORB period not yet complete — scoring 0.")
            return 0

        if "ORB_HIGH" not in df.columns or "ORB_LOW" not in df.columns:
            logger.debug("ORB levels not injected — scoring 0.")
            return 0

        last = df.dropna(subset=["close"]).iloc[-1]
        close = last["close"]
        orb_high = last["ORB_HIGH"]
        orb_low = last["ORB_LOW"]

        if close > orb_high:
            return 1
        elif close < orb_low:
            return -1
        return 0
