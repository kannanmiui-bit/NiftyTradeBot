"""strategy/base_strategy.py — Abstract base for all strategy scorers."""

from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def score(self, df: pd.DataFrame) -> int:
        """
        Return:
          +1  → bullish signal (buy CE)
           0  → neutral / no signal
          -1  → bearish signal (buy PE)
        """
        ...
