"""
run_backtest.py — Standalone backtest runner.
Loads all parameters from .env via config.py — no hardcoded values.

Download historical data first:
    python download_data.py --index NIFTY --days 180

Then run:
    python run_backtest.py --csv historical/nifty_5minute.csv
    python run_backtest.py --csv historical/nifty_5minute.csv --mode buy
"""

import argparse
from datetime import date

from config import load_config
from backtest.engine import BacktestEngine
from backtest.report import print_report, save_trade_log, plot_results
from utils.logger import setup_logger

setup_logger()


def main():
    parser = argparse.ArgumentParser(description="Run strategy backtest")
    parser.add_argument("--csv", required=True, help="Path to OHLCV CSV file")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   default=None, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--mode", default=None, choices=["buy", "sell_spread"],
        help="Override STRATEGY_TYPE from .env"
    )
    args = parser.parse_args()

    config = load_config()

    # --mode flag overrides .env STRATEGY_TYPE
    strategy_type = args.mode or config.strategy_type

    engine = BacktestEngine(
        # ── Strategy params (from .env) ───────────────────────────────────────
        supertrend_period=config.supertrend_period,
        supertrend_mult=config.supertrend_mult,
        rsi_period=config.rsi_period,
        rsi_overbought=config.rsi_overbought,
        rsi_oversold=config.rsi_oversold,
        ema_fast=config.ema_fast,
        ema_slow=config.ema_slow,
        orb_end=config.orb_end,
        vwap_band_pct=config.vwap_band_pct,
        score_threshold=config.score_threshold,
        # ── Risk params (from .env) ───────────────────────────────────────────
        target_pct=config.target_pct,
        sl_pct=config.sl_pct,
        trail_trigger_pct=config.trail_trigger_pct,
        trail_step_pct=config.trail_step_pct,
        max_daily_loss=config.max_daily_loss,
        max_trades_per_day=config.max_trades_per_day,
        no_new_trade_after=config.no_new_trade_after,
        squareoff_time=config.squareoff_time,
        # ── Position sizing (from .env) ───────────────────────────────────────
        lot_size=config.lot_size,
        num_lots=config.num_lots,
        # ── Spread params (from .env) ─────────────────────────────────────────
        strategy_type=strategy_type,
        hedge_otm_strikes=config.hedge_otm_strikes,
        strike_step=config.strike_step,
        # ── Simulation-only params (no live equivalent, tune here) ────────────
        option_premium_pct=0.015,   # rough ATM premium as % of index (buy mode)
        credit_ratio=0.35,          # net credit as % of spread width (sell mode)
        spread_net_delta=0.30,      # net delta of the spread
        sl_loss_multiple=2.0,       # SL when loss = N * net_credit
        target_capture_pct=0.60,    # TP when N% of credit captured
        # ── New features (from .env) ──────────────────────────────────────────
        atr_sl_mult=config.atr_sl_mult,
        use_daily_trend=config.use_daily_trend,
        volume_period=config.volume_period,
        volume_mult=config.volume_mult,
        daily_ema_fast=config.daily_ema_fast,
        daily_ema_slow=config.daily_ema_slow,
        # ── Economic calendar (from .env) ─────────────────────────────────────
        use_calendar=config.use_calendar,
        calendar_skip_rbi=config.calendar_skip_rbi,
        calendar_skip_budget=config.calendar_skip_budget,
        calendar_skip_fed=config.calendar_skip_fed,
        calendar_skip_expiry_after=config.calendar_skip_expiry_after,
        # ── Signal type (from .env) ───────────────────────────────────────────
        signal_type=config.signal_type,
    )

    print(f"\nBacktest config loaded from .env:")
    print(f"  Strategy   : {strategy_type}")
    print(f"  Index      : {config.index} | LotSize={config.lot_size} | Step={config.strike_step}")
    print(f"  Score thr  : {config.score_threshold}")
    print(f"  Target     : {config.target_pct:.0%} | SL: {config.sl_pct:.0%}")
    print(f"  Trail trig : {config.trail_trigger_pct:.0%} | Step: {config.trail_step_pct:.0%}")
    if strategy_type == "sell_spread":
        print(f"  Hedge legs : {config.hedge_otm_strikes} strikes OTM")
    print()

    df = engine.load_csv(args.csv)

    start = date.fromisoformat(args.start) if args.start else None
    end   = date.fromisoformat(args.end)   if args.end   else None

    result = engine.run(df, start_date=start, end_date=end)
    stats = print_report(result)
    csv_path = save_trade_log(result)
    chart_path = plot_results(result)

    print(f"\nTrade log : {csv_path}")
    if chart_path:
        print(f"Chart     : {chart_path}")


if __name__ == "__main__":
    main()
