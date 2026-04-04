"""
strategy/breakout_selector.py — 15-minute Opening Range Breakout with ATR buffer.

Signal logic (works with index data that has no volume):
  - 15-min range  = High/Low of first 3 candles (09:15–09:29)
  - ATR buffer    = atr_buffer_mult × ATR (filters out false tick breakouts)
  - CE signal     : close > 15min_high + ATR_buffer  (confirmed bullish breakout)
  - PE signal     : close < 15min_low  − ATR_buffer  (confirmed bearish breakdown)

If real volume is available, also require volume > VOL_MA × volume_mult.

Requires columns pre-computed by BacktestEngine:
  ORB15_HIGH, ORB15_LOW  — per-day 15-min range
  ATR                    — Average True Range (from IndicatorEngine)
  VOL_MA, volume         — used when volume data is available
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SignalResult:
    direction: Optional[str]       # "CE", "PE", or None
    aggregate_score: int
    individual_scores: dict = field(default_factory=dict)
    timestamp: Optional[datetime] = None

    def __str__(self) -> str:
        scores_str = ", ".join(f"{k}={v}" for k, v in self.individual_scores.items())
        return f"Signal={self.direction or 'NONE'} | [{scores_str}]"


class BreakoutSelector:
    """15-min ORB + ATR buffer breakout signal."""

    def __init__(self, atr_buffer_mult: float = 0.25, volume_mult: float = 1.5):
        self.atr_buffer_mult = atr_buffer_mult  # breakout must exceed range by N × ATR
        self.volume_mult = volume_mult

    def evaluate(self, df: pd.DataFrame) -> SignalResult:
        if df.empty:
            return SignalResult(direction=None, aggregate_score=0)

        required = ["ORB15_HIGH", "ORB15_LOW", "ATR", "close"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.debug(f"BreakoutSelector: missing columns {missing} — no signal.")
            return SignalResult(direction=None, aggregate_score=0)

        last       = df.iloc[-1]
        close      = last["close"]
        orb15_high = last["ORB15_HIGH"]
        orb15_low  = last["ORB15_LOW"]
        atr        = last["ATR"]

        if any(math.isnan(v) for v in [orb15_high, orb15_low, atr]):
            return SignalResult(direction=None, aggregate_score=0)

        # ATR buffer: price must break meaningfully beyond the range
        buffer     = self.atr_buffer_mult * atr
        high_break = close > orb15_high + buffer
        low_break  = close < orb15_low  - buffer

        # Volume confirmation — only when real volume data is available
        volume = last.get("volume", 0)
        vol_ma = last.get("VOL_MA", 0)
        if vol_ma > 0 and volume > 0:
            vol_confirm = volume > vol_ma * self.volume_mult
        else:
            vol_confirm = True  # no volume data — rely on ATR buffer alone

        scores = {
            "orb15_high":  round(orb15_high, 1),
            "orb15_low":   round(orb15_low, 1),
            "buffer":      round(buffer, 1),
            "close":       round(close, 1),
            "high_break":  high_break,
            "low_break":   low_break,
            "vol_confirm": vol_confirm,
        }

        timestamp = df.index[-1] if hasattr(df.index[-1], "to_pydatetime") else None

        if high_break and vol_confirm:
            result = SignalResult(direction="CE", aggregate_score=2,
                                  individual_scores=scores, timestamp=timestamp)
        elif low_break and vol_confirm:
            result = SignalResult(direction="PE", aggregate_score=2,
                                  individual_scores=scores, timestamp=timestamp)
        else:
            result = SignalResult(direction=None, aggregate_score=0,
                                  individual_scores=scores, timestamp=timestamp)

        if result.direction:
            logger.debug(str(result))
        return result
