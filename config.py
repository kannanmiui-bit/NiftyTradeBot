"""
config.py — Central configuration for the Nifty/BankNifty options bot.
All values are loaded from .env via python-dotenv.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default=None, cast=str):
    val = os.getenv(key, default)
    if val is None:
        raise ValueError(f"Missing required env var: {key}")
    return cast(val)


def _opt(key: str, default, cast=str):
    val = os.getenv(key, str(default))
    return cast(val)


@dataclass(frozen=True)
class BotConfig:
    # ── Broker ──────────────────────────────────────────────────────────────
    api_key: str
    api_secret: str
    access_token: str
    totp_secret: str

    # ── Instrument ───────────────────────────────────────────────────────────
    index: str           # "NIFTY" | "BANKNIFTY"
    exchange: str        # "NFO"
    underlying_exchange: str  # "NSE"
    lot_size: int
    strike_step: int
    otm_strikes: int     # 0 = ATM, 1 = 1 OTM, etc.
    num_lots: int

    # ── Strategy type ─────────────────────────────────────────────────────────
    strategy_type: str   # "buy" | "sell_spread"
    hedge_otm_strikes: int  # strikes OTM for hedge leg (sell_spread only)

    # ── Enhanced features ────────────────────────────────────────────────────
    volume_period: int
    volume_mult: float
    atr_sl_mult: float
    use_daily_trend: bool
    daily_ema_fast: int
    daily_ema_slow: int

    # ── Economic calendar ────────────────────────────────────────────────────
    use_calendar: bool
    calendar_skip_rbi: bool
    calendar_skip_budget: bool
    calendar_skip_fed: bool
    calendar_skip_expiry_after: str

    # ── Signal type ──────────────────────────────────────────────────────────
    signal_type: str   # "breakout" | "multi_indicator"

    # ── Strategy params ──────────────────────────────────────────────────────
    supertrend_period: int
    supertrend_mult: float
    rsi_period: int
    rsi_overbought: int
    rsi_oversold: int
    ema_fast: int
    ema_slow: int
    orb_start: str       # "09:15"
    orb_end: str         # "09:45"
    vwap_band_pct: float
    score_threshold: int

    # ── Risk ─────────────────────────────────────────────────────────────────
    target_pct: float
    sl_pct: float
    trail_trigger_pct: float
    trail_step_pct: float
    max_daily_loss: float
    max_trades_per_day: int

    # ── Scheduling ───────────────────────────────────────────────────────────
    candle_interval: str
    poll_seconds: int
    market_open: str
    market_close: str
    no_new_trade_after: str
    squareoff_time: str

    # ── Notifications ────────────────────────────────────────────────────────
    telegram_token: str
    telegram_chat_id: str


def load_config() -> BotConfig:
    return BotConfig(
        # Broker
        api_key=_get("KITE_API_KEY"),
        api_secret=_get("KITE_API_SECRET"),
        access_token=_opt("KITE_ACCESS_TOKEN", ""),
        totp_secret=_opt("KITE_TOTP_SECRET", ""),

        # Instrument
        index=_opt("INDEX", "NIFTY"),
        exchange="NFO",
        underlying_exchange="NSE",
        lot_size=_opt("LOT_SIZE", 65, int),
        strike_step=_opt("STRIKE_STEP", 50, int),
        otm_strikes=_opt("OTM_STRIKES", 0, int),
        num_lots=_opt("NUM_LOTS", 1, int),

        # Strategy type
        strategy_type=_opt("STRATEGY_TYPE", "buy"),
        hedge_otm_strikes=_opt("HEDGE_OTM_STRIKES", 2, int),

        # Enhanced features
        volume_period=_opt("VOLUME_PERIOD", 20, int),
        volume_mult=_opt("VOLUME_MULT", 1.2, float),
        atr_sl_mult=_opt("ATR_SL_MULT", 0.0, float),
        use_daily_trend=_opt("USE_DAILY_TREND", "true").lower() == "true",
        daily_ema_fast=_opt("DAILY_EMA_FAST", 5, int),
        daily_ema_slow=_opt("DAILY_EMA_SLOW", 20, int),

        # Economic calendar
        use_calendar=_opt("USE_CALENDAR", "true").lower() == "true",
        calendar_skip_rbi=_opt("CALENDAR_SKIP_RBI", "true").lower() == "true",
        calendar_skip_budget=_opt("CALENDAR_SKIP_BUDGET", "true").lower() == "true",
        calendar_skip_fed=_opt("CALENDAR_SKIP_FED", "true").lower() == "true",
        calendar_skip_expiry_after=_opt("CALENDAR_SKIP_EXPIRY_AFTER", "13:00"),

        # Signal type
        signal_type=_opt("SIGNAL_TYPE", "breakout"),

        # Strategy
        supertrend_period=_opt("SUPERTREND_PERIOD", 7, int),
        supertrend_mult=_opt("SUPERTREND_MULT", 3.0, float),
        rsi_period=_opt("RSI_PERIOD", 14, int),
        rsi_overbought=_opt("RSI_OVERBOUGHT", 60, int),
        rsi_oversold=_opt("RSI_OVERSOLD", 40, int),
        ema_fast=_opt("EMA_FAST", 9, int),
        ema_slow=_opt("EMA_SLOW", 21, int),
        orb_start=_opt("ORB_START", "09:15"),
        orb_end=_opt("ORB_END", "09:45"),
        vwap_band_pct=_opt("VWAP_BAND_PCT", 0.003, float),
        score_threshold=_opt("SCORE_THRESHOLD", 2, int),

        # Risk
        target_pct=_opt("TARGET_PCT", 0.50, float),
        sl_pct=_opt("SL_PCT", 0.30, float),
        trail_trigger_pct=_opt("TRAIL_TRIGGER_PCT", 0.30, float),
        trail_step_pct=_opt("TRAIL_STEP_PCT", 0.20, float),
        max_daily_loss=_opt("MAX_DAILY_LOSS", -5000, float),
        max_trades_per_day=_opt("MAX_TRADES_PER_DAY", 2, int),

        # Scheduling
        candle_interval=_opt("CANDLE_INTERVAL", "5minute"),
        poll_seconds=_opt("POLL_SECONDS", 5, int),
        market_open=_opt("MARKET_OPEN", "09:15"),
        market_close=_opt("MARKET_CLOSE", "15:20"),
        no_new_trade_after=_opt("NO_NEW_TRADE_AFTER", "14:00"),
        squareoff_time=_opt("SQUAREOFF_TIME", "15:10"),

        # Notifications
        telegram_token=_opt("TELEGRAM_TOKEN", ""),
        telegram_chat_id=_opt("TELEGRAM_CHAT_ID", ""),
    )
