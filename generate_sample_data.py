"""
generate_sample_data.py — Generate synthetic Nifty 5-min OHLCV data for backtesting.

Use this to test the bot without Kite API access.
Creates realistic-looking data with trends, pullbacks, and volatility.

Usage:
    python generate_sample_data.py              # 180 days, NIFTY
    python generate_sample_data.py --days 365
    python generate_sample_data.py --index BANKNIFTY --start-price 48000
"""

import argparse
import os
from datetime import datetime, timedelta, time as dtime

import numpy as np
import pandas as pd
import pytz

IST = pytz.timezone("Asia/Kolkata")
OUT_DIR = "historical"


def generate_nifty_data(
    start_price: float = 22000.0,
    days: int = 180,
    end_date: datetime = None,
    interval_minutes: int = 5,
    volatility: float = 0.0008,       # per-candle % vol
    trend: float = 0.00003,           # slight upward bias per candle
    seed: int = 42,
) -> pd.DataFrame:
    np.random.seed(seed)

    if end_date is None:
        end_date = datetime.now(IST).replace(hour=15, minute=30, second=0, microsecond=0)

    # Build list of all 5-min candles across trading days
    records = []
    current_date = end_date.date() - timedelta(days=days)
    price = start_price
    regime_len = 0
    regime_dir = 1.0  # start bullish

    while current_date <= end_date.date():
        # Skip weekends
        if current_date.weekday() >= 5:
            current_date += timedelta(days=1)
            continue

        # Regime flip every 10–25 days
        if regime_len <= 0:
            regime_dir = np.random.choice([1.0, -1.0], p=[0.6, 0.4])
            regime_len = np.random.randint(10, 25)
        regime_len -= 1

        day_trend = trend * regime_dir

        # Open at previous close ± gap
        open_price = price * (1 + np.random.normal(0, 0.0003))
        price = open_price

        # Generate each 5-min candle within the trading day (09:15–15:30)
        t = datetime.combine(current_date, dtime(9, 15))
        t = IST.localize(t)
        while t.time() <= dtime(15, 25):
            ret = np.random.normal(day_trend, volatility)
            close = price * (1 + ret)

            # OHLC within realistic spread
            spread = abs(close - price) + price * volatility * 0.5
            high = max(price, close) + abs(np.random.normal(0, spread * 0.4))
            low = min(price, close) - abs(np.random.normal(0, spread * 0.4))
            volume = int(np.random.lognormal(10.5, 0.5))

            records.append({
                "datetime": t,
                "open": round(price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": volume,
            })

            price = close
            t += timedelta(minutes=interval_minutes)

        current_date += timedelta(days=1)

    df = pd.DataFrame(records)
    df = df.set_index("datetime").sort_index()
    return df


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic OHLCV data")
    parser.add_argument("--index", default="NIFTY", choices=["NIFTY", "BANKNIFTY"])
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--start-price", type=float, default=None)
    parser.add_argument("--interval", type=int, default=5, help="Candle size in minutes")
    args = parser.parse_args()

    defaults = {"NIFTY": 22000.0, "BANKNIFTY": 48000.0}
    start_price = args.start_price or defaults[args.index]
    vol = 0.0008 if args.index == "NIFTY" else 0.0010

    print(f"Generating {args.days} days of synthetic {args.index} {args.interval}-min data...")
    df = generate_nifty_data(
        start_price=start_price,
        days=args.days,
        interval_minutes=args.interval,
        volatility=vol,
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"{args.index.lower()}_{args.interval}minute.csv")
    df.reset_index().to_csv(out, index=False)

    print(f"Saved {len(df):,} candles to: {out}")
    print(f"Date range: {df.index[0].date()} to {df.index[-1].date()}")
    print(f"Price range: {df['close'].min():.0f} – {df['close'].max():.0f}")
    print(f"\nRun backtest:")
    print(f"  python run_backtest.py --csv {out} --index {args.index}")


if __name__ == "__main__":
    main()
