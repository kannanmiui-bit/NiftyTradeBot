"""
run_backtest.py — Standalone backtest runner.

Download historical Nifty 5-min data first:
    pip install jugaad-data
    python -c "
from jugaad_data.nse import index_raw
import pandas as pd
df = index_raw('NIFTY 50', '01-01-2024', '31-12-2024')
df.to_csv('data/nifty_5min.csv')
"

Then run:
    python run_backtest.py --csv data/nifty_5min.csv
"""

import argparse
from datetime import date

from backtest.engine import BacktestEngine
from backtest.report import print_report, save_trade_log, plot_results
from utils.logger import setup_logger

setup_logger()


def main():
    parser = argparse.ArgumentParser(description="Run strategy backtest")
    parser.add_argument("--csv", required=True, help="Path to OHLCV CSV file")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--index", default="NIFTY", choices=["NIFTY", "BANKNIFTY"],
        help="Index to backtest"
    )
    args = parser.parse_args()

    lot_size = 50 if args.index == "NIFTY" else 15
    strike_step = 50 if args.index == "NIFTY" else 100

    engine = BacktestEngine(
        # Strategy defaults (tune these)
        supertrend_period=7,
        supertrend_mult=3.0,
        rsi_period=14,
        rsi_overbought=60,
        rsi_oversold=40,
        ema_fast=9,
        ema_slow=21,
        orb_end="09:45",
        vwap_band_pct=0.003,
        score_threshold=2,
        # Risk defaults
        target_pct=0.50,
        sl_pct=0.30,
        trail_trigger_pct=0.30,
        trail_step_pct=0.20,
        max_daily_loss=-5000,
        max_trades_per_day=2,
        no_new_trade_after="14:00",
        squareoff_time="15:10",
        # Position sizing
        lot_size=lot_size,
        num_lots=1,
    )

    df = engine.load_csv(args.csv)

    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None

    result = engine.run(df, start_date=start, end_date=end)
    stats = print_report(result)
    csv_path = save_trade_log(result)
    chart_path = plot_results(result)

    print(f"\nTrade log : {csv_path}")
    if chart_path:
        print(f"Chart     : {chart_path}")


if __name__ == "__main__":
    main()
