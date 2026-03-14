"""
backtest/engine.py — Offline backtesting engine.

Replays the same strategy/indicator/risk code against historical OHLCV data.
Uses the same StrategySelector and IndicatorEngine as live — no separate reimplementation.

Usage:
    engine = BacktestEngine(config_kwargs)
    df = engine.load_csv("nifty_5min.csv")
    result = engine.run(df, start_date=date(2024, 1, 1), end_date=date(2024, 6, 30))
    engine.print_report(result)
"""

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from typing import Optional

import pandas as pd
import pytz

from data.indicators import IndicatorEngine
from strategy.selector import StrategySelector, SignalResult
from utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class BacktestTrade:
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_time: datetime
    exit_time: datetime
    exit_reason: str
    pnl: float
    pnl_pct: float
    signal_scores: dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


class BacktestState:
    """Holds mutable state during the replay loop."""
    def __init__(self):
        self.open_trade: Optional[dict] = None
        self.closed_trades: list[BacktestTrade] = []
        self.daily_pnl: float = 0.0
        self.trade_count_today: int = 0
        self.current_date: Optional[date] = None
        self.equity: float = 0.0

    def reset_day(self, new_date: date):
        if self.current_date != new_date:
            self.daily_pnl = 0.0
            self.trade_count_today = 0
            self.current_date = new_date


class BacktestEngine:
    def __init__(
        self,
        # Strategy params (mirror config defaults)
        supertrend_period: int = 7,
        supertrend_mult: float = 3.0,
        rsi_period: int = 14,
        rsi_overbought: int = 60,
        rsi_oversold: int = 40,
        ema_fast: int = 9,
        ema_slow: int = 21,
        orb_end: str = "09:45",
        vwap_band_pct: float = 0.003,
        score_threshold: int = 2,
        # Risk params
        target_pct: float = 0.50,
        sl_pct: float = 0.30,
        trail_trigger_pct: float = 0.30,
        trail_step_pct: float = 0.20,
        max_daily_loss: float = -5000.0,
        max_trades_per_day: int = 2,
        no_new_trade_after: str = "14:00",
        squareoff_time: str = "15:10",
        # Options params
        lot_size: int = 50,
        num_lots: int = 1,
        # Premium approximation (when no options CSV)
        option_premium_pct: float = 0.015,  # 1.5% of index as rough ATM premium
    ):
        self.indicators = IndicatorEngine(
            supertrend_period, supertrend_mult, rsi_period, ema_fast, ema_slow
        )
        self.selector = StrategySelector(
            score_threshold, supertrend_period, supertrend_mult,
            rsi_period, rsi_overbought, rsi_oversold,
            ema_fast, ema_slow, orb_end, vwap_band_pct,
        )
        self.target_pct = target_pct
        self.sl_pct = sl_pct
        self.trail_trigger_pct = trail_trigger_pct
        self.trail_step_pct = trail_step_pct
        self.max_daily_loss = max_daily_loss
        self.max_trades_per_day = max_trades_per_day
        self.lot_size = lot_size
        self.num_lots = num_lots
        self.quantity = lot_size * num_lots
        self.option_premium_pct = option_premium_pct

        nt_h, nt_m = map(int, no_new_trade_after.split(":"))
        self._no_new_trade_after = dtime(nt_h, nt_m)
        sq_h, sq_m = map(int, squareoff_time.split(":"))
        self._squareoff_time = dtime(sq_h, sq_m)
        orb_h, orb_m = map(int, orb_end.split(":"))
        self._orb_end = dtime(orb_h, orb_m)

    # ── Data loading ─────────────────────────────────────────────────────────

    def load_csv(self, path: str) -> pd.DataFrame:
        """
        Load OHLCV CSV.
        Expected columns: datetime (or date), open, high, low, close, volume
        """
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"\nCSV file not found: '{path}'\n\n"
                "To get historical data, choose one of:\n"
                "  A) Download from Kite API (requires valid access token):\n"
                "       python generate_token.py\n"
                "       python download_data.py --index NIFTY --days 180\n"
                "       python run_backtest.py --csv historical/nifty_5minute.csv\n\n"
                "  B) Generate synthetic data (no login needed):\n"
                "       python generate_sample_data.py --index NIFTY --days 180\n"
                "       python run_backtest.py --csv historical/nifty_5minute.csv\n"
            )
        df = pd.read_csv(path, parse_dates=["datetime"])
        df = df.set_index("datetime").sort_index()
        if df.index.tz is None:
            df.index = df.index.tz_localize(IST)
        else:
            df.index = df.index.tz_convert(IST)
        return df[["open", "high", "low", "close", "volume"]]

    # ── Main replay loop ──────────────────────────────────────────────────────

    def run(
        self,
        df: pd.DataFrame,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> BacktestResult:
        if start_date:
            df = df[df.index.date >= start_date]
        if end_date:
            df = df[df.index.date <= end_date]

        state = BacktestState()
        equity_curve = []
        warmup = max(50, self.indicators.ema_slow * 3)  # candles needed

        logger.info(f"Backtesting {len(df)} candles from {df.index[0]} to {df.index[-1]}")

        for i in range(warmup, len(df)):
            candle_time: datetime = df.index[i]
            candle = df.iloc[i]
            today = candle_time.date()
            t = candle_time.time()

            # Reset daily counters at day boundary
            state.reset_day(today)

            # Only process market hours
            if not (dtime(9, 15) <= t <= dtime(15, 30)):
                continue

            # ── Manage open trade ──────────────────────────────────────────
            if state.open_trade:
                exit_reason = self._check_exit(state.open_trade, candle, t)
                if exit_reason:
                    self._close_trade(state, exit_reason, candle, candle_time)
                    equity_curve.append(state.equity)
                continue

            # ── Signal generation ──────────────────────────────────────────
            # Risk gates
            if state.trade_count_today >= self.max_trades_per_day:
                continue
            if state.daily_pnl <= self.max_daily_loss:
                continue
            if t >= self._no_new_trade_after:
                continue
            if t < self._orb_end:
                continue  # wait for ORB to establish

            # Build incremental DataFrame and compute indicators
            current_df = df.iloc[:i+1]
            orb_levels = self._compute_orb(current_df, today)
            orb_high, orb_low = orb_levels if orb_levels else (None, None)
            enriched = self.indicators.compute_all(current_df, orb_high, orb_low)

            signal = self.selector.evaluate(enriched)

            if signal.direction is None:
                continue

            # ── Open trade ─────────────────────────────────────────────────
            entry_price = self._estimate_option_premium(candle["close"], signal.direction)
            state.open_trade = {
                "symbol": f"{signal.direction}_BT",
                "direction": signal.direction,
                "entry_price": entry_price,
                "entry_time": candle_time,
                "peak_price": entry_price,
                "trailing_activated": False,
                "signal_scores": signal.individual_scores.copy(),
            }
            state.trade_count_today += 1
            logger.debug(
                f"BT ENTRY | {candle_time} | {signal.direction} | "
                f"premium={entry_price:.2f} | score={signal.aggregate_score}"
            )

        # Close any open trade at end of data
        if state.open_trade:
            last_candle = df.iloc[-1]
            self._close_trade(state, "DATA_END", last_candle, df.index[-1])
            equity_curve.append(state.equity)

        result = BacktestResult(trades=state.closed_trades, equity_curve=equity_curve)
        logger.info(
            f"Backtest complete: {len(state.closed_trades)} trades | "
            f"Final equity={state.equity:+.2f}"
        )
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _estimate_option_premium(self, index_ltp: float, direction: str) -> float:
        """
        Rough ATM option premium estimate (1.5% of index by default).
        Replace with actual historical options CSV lookup for accuracy.
        """
        return round(index_ltp * self.option_premium_pct, 2)

    def _compute_orb(self, df: pd.DataFrame, today: date):
        today_df = df[df.index.date == today]
        orb_df = today_df.between_time("09:15", "09:45")
        if orb_df.empty:
            return None
        return orb_df["high"].max(), orb_df["low"].min()

    def _check_exit(self, trade: dict, candle: pd.Series, t: dtime) -> Optional[str]:
        """
        Check exit conditions using candle OHLC (conservative: assume SL before target).
        """
        entry = trade["entry_price"]
        peak = trade["peak_price"]

        # Update peak using candle high
        if candle["high"] > peak:
            trade["peak_price"] = candle["high"]
            if not trade["trailing_activated"]:
                peak_profit = (trade["peak_price"] - entry) / entry
                if peak_profit >= self.trail_trigger_pct:
                    trade["trailing_activated"] = True

        # Check stop loss (use candle low — conservative)
        sl_price = entry * (1 - self.sl_pct)
        if candle["low"] <= sl_price:
            return "STOP_LOSS"

        # Check target (use candle high)
        target_price = entry * (1 + self.target_pct)
        if candle["high"] >= target_price:
            return "TARGET"

        # Check trailing SL (use candle low)
        if trade["trailing_activated"]:
            trail_sl = trade["peak_price"] * (1 - self.trail_step_pct)
            if candle["low"] <= trail_sl:
                return "TRAILING_SL"

        # Time-based squareoff
        if t >= self._squareoff_time:
            return "SQUAREOFF"

        return None

    def _close_trade(
        self,
        state: BacktestState,
        reason: str,
        candle: pd.Series,
        exit_time: datetime,
    ):
        trade = state.open_trade
        entry = trade["entry_price"]

        # Determine exit price based on reason
        if reason == "STOP_LOSS":
            exit_price = entry * (1 - self.sl_pct)
        elif reason == "TARGET":
            exit_price = entry * (1 + self.target_pct)
        elif reason == "TRAILING_SL":
            exit_price = trade["peak_price"] * (1 - self.trail_step_pct)
        else:
            exit_price = candle["close"]

        pnl = (exit_price - entry) * self.quantity
        pnl_pct = (exit_price - entry) / entry

        bt_trade = BacktestTrade(
            symbol=trade["symbol"],
            direction=trade["direction"],
            entry_price=entry,
            exit_price=exit_price,
            quantity=self.quantity,
            entry_time=trade["entry_time"],
            exit_time=exit_time,
            exit_reason=reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
            signal_scores=trade["signal_scores"],
        )
        state.closed_trades.append(bt_trade)
        state.daily_pnl += pnl
        state.equity += pnl
        state.open_trade = None

        logger.debug(
            f"BT EXIT | {exit_time} | {reason} | "
            f"Entry={entry:.2f} Exit={exit_price:.2f} P&L={pnl:+.2f}"
        )
