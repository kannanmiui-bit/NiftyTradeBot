"""
data/indicators.py — Technical indicator computation.

Uses the `ta` library (pip install ta) for RSI, EMA, VWAP.
Supertrend is implemented manually with pandas (no external dependency).

Compatible with Python 3.14+ (no numba/TA-Lib required).
"""

import numpy as np
import pandas as pd
import ta

from utils.logger import get_logger

logger = get_logger(__name__)


class IndicatorEngine:
    def __init__(
        self,
        supertrend_period: int = 7,
        supertrend_mult: float = 3.0,
        rsi_period: int = 14,
        ema_fast: int = 9,
        ema_slow: int = 21,
        volume_period: int = 20,
        daily_ema_fast: int = 5,
        daily_ema_slow: int = 20,
    ):
        self.supertrend_period = supertrend_period
        self.supertrend_mult = supertrend_mult
        self.rsi_period = rsi_period
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.volume_period = volume_period
        self.daily_ema_fast = daily_ema_fast
        self.daily_ema_slow = daily_ema_slow

    def compute_all(
        self,
        df: pd.DataFrame,
        orb_high: float = None,
        orb_low: float = None,
    ) -> pd.DataFrame:
        """
        Compute all indicators on the given OHLCV DataFrame.
        orb_high / orb_low are injected as constant columns if provided.
        Returns enriched DataFrame (copy).
        """
        df = df.copy()
        min_candles = max(self.supertrend_period, self.rsi_period, self.ema_slow) + 10
        if len(df) < min_candles:
            logger.warning(
                f"Only {len(df)} candles — need {min_candles} for reliable indicators."
            )

        df = self._add_supertrend(df)
        df = self._add_rsi(df)
        df = self._add_ema(df)
        df = self._add_vwap(df)
        df = self._add_atr(df)
        df = self._add_volume_metrics(df)
        df = self._add_daily_trend(df)

        if orb_high is not None and orb_low is not None:
            df["ORB_HIGH"] = orb_high
            df["ORB_LOW"] = orb_low

        return df

    # ── Supertrend (manual implementation) ───────────────────────────────────

    def _add_supertrend(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Supertrend indicator.
        Adds columns:
          SUPERT_{period}_{mult}   — the supertrend line value
          SUPERTd_{period}_{mult}  — direction: +1 (bullish) / -1 (bearish)
        """
        period = self.supertrend_period
        mult = self.supertrend_mult
        col_val = f"SUPERT_{period}_{mult}"
        col_dir = f"SUPERTd_{period}_{mult}"

        high = df["high"]
        low = df["low"]
        close = df["close"]

        # Average True Range (ATR)
        hl = high - low
        hc = (high - close.shift(1)).abs()
        lc = (low - close.shift(1)).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()

        hl2 = (high + low) / 2
        upper_band = hl2 + mult * atr
        lower_band = hl2 - mult * atr

        supertrend = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=float)

        for i in range(1, len(df)):
            # Upper band
            if upper_band.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1]:
                upper_band.iloc[i] = upper_band.iloc[i]
            else:
                upper_band.iloc[i] = upper_band.iloc[i - 1]

            # Lower band
            if lower_band.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1]:
                lower_band.iloc[i] = lower_band.iloc[i]
            else:
                lower_band.iloc[i] = lower_band.iloc[i - 1]

            # Direction
            prev_st = supertrend.iloc[i - 1] if i > 1 else upper_band.iloc[i]
            if pd.isna(prev_st):
                supertrend.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
            elif prev_st == upper_band.iloc[i - 1]:
                # Was bearish
                if close.iloc[i] <= upper_band.iloc[i]:
                    supertrend.iloc[i] = upper_band.iloc[i]
                    direction.iloc[i] = -1
                else:
                    supertrend.iloc[i] = lower_band.iloc[i]
                    direction.iloc[i] = 1
            else:
                # Was bullish
                if close.iloc[i] >= lower_band.iloc[i]:
                    supertrend.iloc[i] = lower_band.iloc[i]
                    direction.iloc[i] = 1
                else:
                    supertrend.iloc[i] = upper_band.iloc[i]
                    direction.iloc[i] = -1

        df[col_val] = supertrend
        df[col_dir] = direction
        return df

    # ── RSI ───────────────────────────────────────────────────────────────────

    def _add_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        col = f"RSI_{self.rsi_period}"
        rsi = ta.momentum.RSIIndicator(
            close=df["close"], window=self.rsi_period
        ).rsi()
        df[col] = rsi
        return df

    # ── EMA ───────────────────────────────────────────────────────────────────

    def _add_ema(self, df: pd.DataFrame) -> pd.DataFrame:
        df[f"EMA_{self.ema_fast}"] = ta.trend.EMAIndicator(
            close=df["close"], window=self.ema_fast
        ).ema_indicator()
        df[f"EMA_{self.ema_slow}"] = ta.trend.EMAIndicator(
            close=df["close"], window=self.ema_slow
        ).ema_indicator()
        return df

    # ── VWAP ──────────────────────────────────────────────────────────────────

    def _add_vwap(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        VWAP that resets daily.
        Iterates by date group to ensure proper intraday reset.
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            logger.warning("VWAP skipped: DataFrame lacks DatetimeIndex.")
            return df

        vwap_col = pd.Series(index=df.index, dtype=float)

        for day, group in df.groupby(df.index.date):
            if len(group) < 2:
                continue
            try:
                vwap = ta.volume.VolumeWeightedAveragePrice(
                    high=group["high"],
                    low=group["low"],
                    close=group["close"],
                    volume=group["volume"],
                ).volume_weighted_average_price()
                vwap_col.loc[group.index] = vwap.values
            except Exception as e:
                logger.debug(f"VWAP failed for {day}: {e}")

        df["VWAP_D"] = vwap_col
        return df

    # ── ATR ───────────────────────────────────────────────────────────────────

    def _add_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        """Expose ATR as a column (reuses TR computation from supertrend)."""
        high, low, close = df["high"], df["low"], df["close"]
        hl = high - low
        hc = (high - close.shift(1)).abs()
        lc = (low - close.shift(1)).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df["ATR"] = tr.ewm(span=self.supertrend_period, adjust=False).mean()
        return df

    # ── Volume ────────────────────────────────────────────────────────────────

    def _add_volume_metrics(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rolling average volume for volume confirmation scoring."""
        df["VOL_MA"] = df["volume"].rolling(self.volume_period).mean()
        return df

    # ── Daily trend ───────────────────────────────────────────────────────────

    def _add_daily_trend(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute EMA fast/slow on daily closes and map back to intraday rows.
        Uses previous day's signal to avoid lookahead bias.
        Adds DAILY_TREND column: +1 (uptrend), -1 (downtrend), 0 (insufficient data).
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            df["DAILY_TREND"] = 0
            return df

        daily_close = df.groupby(df.index.date)["close"].last()
        if len(daily_close) < self.daily_ema_slow + 1:
            df["DAILY_TREND"] = 0
            return df

        ema_fast = daily_close.ewm(span=self.daily_ema_fast, adjust=False).mean()
        ema_slow = daily_close.ewm(span=self.daily_ema_slow, adjust=False).mean()
        trend = np.where(ema_fast > ema_slow, 1, -1)
        trend_series = pd.Series(trend, index=daily_close.index)

        # Shift by 1 day: use yesterday's daily trend for today's trades
        trend_shifted = trend_series.shift(1)
        date_to_trend = trend_shifted.to_dict()  # {date: +1/-1/NaN}

        df["DAILY_TREND"] = [
            int(date_to_trend[ts.date()])
            if ts.date() in date_to_trend and not pd.isna(date_to_trend[ts.date()])
            else 0
            for ts in df.index
        ]
        return df

    # ── Convenience accessors ─────────────────────────────────────────────────

    @property
    def supertrend_dir_col(self) -> str:
        return f"SUPERTd_{self.supertrend_period}_{self.supertrend_mult}"

    @property
    def rsi_col(self) -> str:
        return f"RSI_{self.rsi_period}"

    @property
    def ema_fast_col(self) -> str:
        return f"EMA_{self.ema_fast}"

    @property
    def ema_slow_col(self) -> str:
        return f"EMA_{self.ema_slow}"
