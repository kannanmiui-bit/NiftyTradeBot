"""
download_data.py — Download historical Nifty/BankNifty OHLCV data via Kite API.

Kite API allows intraday (≤60 days) and daily (longer) historical data.
Fetches in 50-day chunks to stay within the API limit.

Usage:
    python download_data.py                          # NIFTY, last 6 months, 5min
    python download_data.py --index BANKNIFTY        # BankNifty
    python download_data.py --days 365 --interval 15minute
    python download_data.py --start 2024-01-01 --end 2024-12-31
"""

import argparse
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import pytz
from kiteconnect import KiteConnect
from dotenv import load_dotenv

load_dotenv()

IST = pytz.timezone("Asia/Kolkata")

# Zerodha instrument tokens for indices (these are fixed)
INDEX_TOKENS = {
    "NIFTY":     256265,    # NSE:NIFTY 50
    "BANKNIFTY": 260105,    # NSE:NIFTY BANK
    "MIDCPNIFTY": 288009,   # NSE:NIFTY MID SELECT
    "FINNIFTY":  257801,    # NSE:NIFTY FIN SERVICE
}

# Max days Kite allows per intraday request
MAX_CHUNK_DAYS = 50


def fetch_historical(kite: KiteConnect, token: int, from_dt: datetime, to_dt: datetime, interval: str) -> pd.DataFrame:
    """Fetch OHLCV in chunks and concatenate."""
    all_records = []
    current = from_dt

    while current < to_dt:
        chunk_end = min(current + timedelta(days=MAX_CHUNK_DAYS), to_dt)
        print(f"  Fetching {current.date()} → {chunk_end.date()} ...", end=" ", flush=True)

        try:
            records = kite.historical_data(
                instrument_token=token,
                from_date=current,
                to_date=chunk_end,
                interval=interval,
                continuous=False,
                oi=False,
            )
            all_records.extend(records)
            print(f"{len(records)} candles")
        except Exception as e:
            print(f"ERROR: {e}")

        current = chunk_end + timedelta(seconds=1)
        time.sleep(0.4)  # Kite rate limit: ~3 requests/second

    if not all_records:
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    df = df.rename(columns={"date": "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])

    if df["datetime"].dt.tz is None:
        df["datetime"] = df["datetime"].dt.tz_localize(IST)
    else:
        df["datetime"] = df["datetime"].dt.tz_convert(IST)

    df = df.set_index("datetime").sort_index()
    df = df[["open", "high", "low", "close", "volume"]]

    # Keep only market hours
    df = df.between_time("09:15", "15:30")
    df = df.dropna()

    return df


def main():
    parser = argparse.ArgumentParser(description="Download Nifty/BankNifty historical data")
    parser.add_argument("--index", default="NIFTY", choices=list(INDEX_TOKENS.keys()))
    parser.add_argument("--interval", default="5minute",
                        choices=["minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute", "day"])
    parser.add_argument("--days", type=int, default=180, help="Days of history to download (default: 180)")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (overrides --days)")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--out", default=None, help="Output CSV path (default: historical/<index>_<interval>.csv)")
    args = parser.parse_args()

    # Credentials from .env
    api_key = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")
    access_token = os.getenv("KITE_ACCESS_TOKEN", "").strip()

    if not api_key or not access_token:
        print("ERROR: KITE_API_KEY and KITE_ACCESS_TOKEN must be set in .env")
        print("Generate access token at: https://kite.trade/connect/login?api_key=<your_key>")
        return

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    # Verify connection
    try:
        profile = kite.profile()
        print(f"Logged in as: {profile['user_name']} ({profile['user_id']})")
    except Exception as e:
        print(f"Authentication failed: {e}")
        print("Your KITE_ACCESS_TOKEN may have expired. Generate a new one via Kite login.")
        return

    # Date range
    now = datetime.now(IST)
    if args.end:
        to_dt = IST.localize(datetime.strptime(args.end, "%Y-%m-%d").replace(hour=15, minute=30))
    else:
        to_dt = now

    if args.start:
        from_dt = IST.localize(datetime.strptime(args.start, "%Y-%m-%d").replace(hour=9, minute=15))
    else:
        from_dt = to_dt - timedelta(days=args.days)

    token = INDEX_TOKENS[args.index]
    out_path = args.out or os.path.join("historical", f"{args.index.lower()}_{args.interval}.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print(f"\nDownloading {args.index} {args.interval} candles")
    print(f"Range: {from_dt.date()} → {to_dt.date()}")
    print(f"Token: {token}")
    print()

    df = fetch_historical(kite, token, from_dt, to_dt, args.interval)

    if df.empty:
        print("No data downloaded.")
        return

    # Save with datetime as a column (not index) for easy CSV loading
    df.reset_index().to_csv(out_path, index=False)
    print(f"\nSaved {len(df)} candles to: {out_path}")
    print(f"Date range in file: {df.index[0]} → {df.index[-1]}")
    print(f"\nRun backtest with:")
    print(f"  python run_backtest.py --csv {out_path} --index {args.index}")


if __name__ == "__main__":
    main()
