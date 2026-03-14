"""
data/market_data.py — OHLCV data management.
Handles historical candle loading and live WebSocket tick aggregation.
"""

import threading
from collections import deque
from datetime import datetime, timedelta, time as dtime
from typing import Optional, Tuple

import pandas as pd
import pytz
from kiteconnect import KiteTicker

from broker.kite_client import KiteClient
from utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# Candle interval in minutes (for tick aggregation)
INTERVAL_MINUTES = {
    "3minute": 3, "5minute": 5, "10minute": 10,
    "15minute": 15, "30minute": 30, "60minute": 60,
}


class MarketDataManager:
    def __init__(self, kite_client: KiteClient, candle_interval: str = "5minute"):
        self.kite = kite_client
        self.interval = candle_interval
        self.interval_minutes = INTERVAL_MINUTES.get(candle_interval, 5)

        # {instrument_token: deque of OHLCV dicts}
        self._candle_buffers: dict[int, deque] = {}
        # {instrument_token: current incomplete candle dict}
        self._current_candle: dict[int, Optional[dict]] = {}
        self._lock = threading.Lock()

        # ORB levels: {instrument_token: (orb_high, orb_low)}
        self._orb_levels: dict[int, Tuple[float, float]] = {}
        self._orb_established: dict[int, bool] = {}

        self._ticker: Optional[KiteTicker] = None

    # ── Historical data ───────────────────────────────────────────────────────

    def load_historical_candles(
        self,
        instrument_token: int,
        days_back: int = 5,
    ) -> pd.DataFrame:
        """
        Fetch enough historical candles to warm up all indicators.
        Stores them in the candle buffer and returns the DataFrame.
        """
        now = datetime.now(IST)
        # Skip to last trading day if weekend
        from_dt = now - timedelta(days=days_back + 2)  # extra buffer for weekends
        logger.info(
            f"Loading {days_back} days of {self.interval} candles "
            f"for token={instrument_token}..."
        )
        df = self.kite.get_historical(
            instrument_token, from_dt, now, self.interval
        )
        if df.empty:
            logger.warning("No historical data returned.")
            return df

        # Keep only market hours (09:15 – 15:30 IST)
        df = df.between_time("09:15", "15:30")
        df = df.dropna()

        with self._lock:
            self._candle_buffers[instrument_token] = deque(
                df.reset_index().to_dict("records"), maxlen=500
            )
        logger.info(f"Loaded {len(df)} candles for token={instrument_token}.")
        return df

    def get_current_candles(self, instrument_token: int) -> pd.DataFrame:
        """Return completed candles as a DatetimeIndex DataFrame."""
        with self._lock:
            buf = self._candle_buffers.get(instrument_token, deque())
            records = list(buf)

        if not records:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(records)
        # Normalise index column name (historical gives 'datetime', ticks give 'datetime' too)
        if "datetime" in df.columns:
            df = df.set_index("datetime")
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize(IST)
        else:
            df.index = df.index.tz_convert(IST)
        return df[["open", "high", "low", "close", "volume"]].sort_index()

    # ── ORB levels ────────────────────────────────────────────────────────────

    def compute_orb_levels(
        self,
        instrument_token: int,
        orb_start: str = "09:15",
        orb_end: str = "09:45",
    ) -> Optional[Tuple[float, float]]:
        """
        Compute Opening Range Breakout levels from today's candles.
        Returns (orb_high, orb_low) or None if ORB period not yet complete.
        """
        now = datetime.now(IST).time()
        h, m = map(int, orb_end.split(":"))
        if now < dtime(h, m):
            return None  # ORB not yet established

        df = self.get_current_candles(instrument_token)
        today = datetime.now(IST).date()
        today_df = df[df.index.date == today]
        orb_df = today_df.between_time(orb_start, orb_end)

        if orb_df.empty:
            return None

        orb_high = orb_df["high"].max()
        orb_low = orb_df["low"].min()
        with self._lock:
            self._orb_levels[instrument_token] = (orb_high, orb_low)
            self._orb_established[instrument_token] = True
        logger.info(f"ORB levels: High={orb_high:.2f}, Low={orb_low:.2f}")
        return orb_high, orb_low

    def get_orb_levels(self, instrument_token: int) -> Optional[Tuple[float, float]]:
        return self._orb_levels.get(instrument_token)

    # ── Index LTP ─────────────────────────────────────────────────────────────

    def get_index_ltp(self, index_name: str = "NIFTY 50") -> float:
        """Fetch the current LTP of the underlying index."""
        key = f"NSE:{index_name}"
        data = self.kite.get_ltp([key])
        return data[key]["last_price"]

    # ── Live WebSocket streaming ───────────────────────────────────────────────

    def start_streaming(self, instrument_tokens: list[int]):
        """Subscribe to KiteTicker and start aggregating ticks into candles."""
        self._ticker = self.kite.get_ticker()

        # Initialise empty current candles for each token
        with self._lock:
            for token in instrument_tokens:
                if token not in self._candle_buffers:
                    self._candle_buffers[token] = deque(maxlen=500)
                self._current_candle[token] = None

        self._ticker.on_ticks = self._on_ticks
        self._ticker.on_connect = self._on_connect
        self._ticker.on_close = self._on_close
        self._ticker.on_error = self._on_error

        self._streaming_tokens = instrument_tokens
        self._ticker.connect(threaded=True)

    def _on_connect(self, ws, response):
        logger.info("WebSocket connected. Subscribing to instruments...")
        ws.subscribe(self._streaming_tokens)
        ws.set_mode(ws.MODE_FULL, self._streaming_tokens)

    def _on_close(self, ws, code, reason):
        logger.warning(f"WebSocket closed: code={code}, reason={reason}")

    def _on_error(self, ws, code, exception):
        logger.error(f"WebSocket error: code={code}, exception={exception}")

    def _on_ticks(self, ws, ticks: list):
        for tick in ticks:
            token = tick["instrument_token"]
            ltp = tick.get("last_price", 0)
            volume = tick.get("volume", 0)
            ts: datetime = tick.get("exchange_timestamp", datetime.now(IST))
            if ts.tzinfo is None:
                ts = IST.localize(ts)

            # Determine candle slot for this tick
            slot = self._candle_slot(ts)

            with self._lock:
                cur = self._current_candle.get(token)

                if cur is None or cur["datetime"] != slot:
                    # Finalise previous candle if it exists
                    if cur is not None:
                        self._candle_buffers[token].append(cur)
                    # Start new candle
                    self._current_candle[token] = {
                        "datetime": slot,
                        "open": ltp,
                        "high": ltp,
                        "low": ltp,
                        "close": ltp,
                        "volume": volume,
                    }
                else:
                    # Update current candle
                    cur["high"] = max(cur["high"], ltp)
                    cur["low"] = min(cur["low"], ltp)
                    cur["close"] = ltp
                    cur["volume"] = volume

    def _candle_slot(self, ts: datetime) -> datetime:
        """Round timestamp down to the start of the current candle interval."""
        minutes = (ts.minute // self.interval_minutes) * self.interval_minutes
        return ts.replace(minute=minutes, second=0, microsecond=0)

    def stop_streaming(self):
        if self._ticker:
            self._ticker.close()
            logger.info("WebSocket streaming stopped.")
