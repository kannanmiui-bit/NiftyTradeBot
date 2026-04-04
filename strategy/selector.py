"""
strategy/selector.py — Multi-strategy confluence engine.
Aggregates scores from all strategies and emits a trade signal.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from strategy.supertrend_strategy import SupertrendStrategy
from strategy.rsi_strategy import RSIStrategy
from strategy.orb_strategy import ORBStrategy
from strategy.ema_strategy import EMAStrategy
from strategy.vwap_strategy import VWAPStrategy
from strategy.volume_strategy import VolumeStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SignalResult:
    direction: Optional[str]          # "CE", "PE", or None
    aggregate_score: int
    individual_scores: dict = field(default_factory=dict)
    timestamp: Optional[datetime] = None

    def __str__(self) -> str:
        scores_str = ", ".join(f"{k}={v:+d}" for k, v in self.individual_scores.items())
        return (
            f"Signal={self.direction or 'NONE'} | "
            f"Score={self.aggregate_score:+d} | [{scores_str}]"
        )


class StrategySelector:
    def __init__(
        self,
        score_threshold: int = 2,
        supertrend_period: int = 7,
        supertrend_mult: float = 3.0,
        rsi_period: int = 14,
        rsi_overbought: int = 60,
        rsi_oversold: int = 40,
        ema_fast: int = 9,
        ema_slow: int = 21,
        orb_end: str = "09:45",
        vwap_band_pct: float = 0.003,
        volume_period: int = 20,
        volume_mult: float = 1.2,
    ):
        self.score_threshold = score_threshold
        self.strategies = [
            SupertrendStrategy(supertrend_period, supertrend_mult),
            RSIStrategy(rsi_period, rsi_overbought, rsi_oversold),
            ORBStrategy(orb_end),
            EMAStrategy(ema_fast, ema_slow),
            VWAPStrategy(vwap_band_pct),
            VolumeStrategy(volume_period, volume_mult),
        ]

    def evaluate(self, df: pd.DataFrame) -> SignalResult:
        """
        Run all strategies and aggregate scores.

        Returns SignalResult with:
          direction = "CE"  if aggregate >= +threshold
          direction = "PE"  if aggregate <= -threshold
          direction = None  otherwise
        """
        if df.empty or len(df) < 5:
            logger.debug("Insufficient data for strategy evaluation.")
            return SignalResult(direction=None, aggregate_score=0)

        scores = {}
        for strategy in self.strategies:
            try:
                scores[strategy.name] = strategy.score(df)
            except Exception as e:
                logger.warning(f"Strategy {strategy.name} raised error: {e}. Scoring 0.")
                scores[strategy.name] = 0

        aggregate = sum(scores.values())
        timestamp = df.index[-1] if hasattr(df.index[-1], "to_pydatetime") else None

        if aggregate >= self.score_threshold:
            direction = "CE"
        elif aggregate <= -self.score_threshold:
            direction = "PE"
        else:
            direction = None

        result = SignalResult(
            direction=direction,
            aggregate_score=aggregate,
            individual_scores=scores,
            timestamp=timestamp,
        )
        logger.debug(str(result))
        return result
