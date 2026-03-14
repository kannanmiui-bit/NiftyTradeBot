"""
broker/kite_client.py — Zerodha Kite API wrapper with auth lifecycle management.
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Optional

import pyotp
import requests
import pytz
from kiteconnect import KiteConnect, KiteTicker

from utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# Kite rate limits
_QUOTE_RATE_LIMIT = 1.0      # seconds between quote calls
_ORDER_RATE_LIMIT = 0.12     # ~8/second
_HIST_RATE_LIMIT = 0.35      # ~3/second


class KiteClient:
    def __init__(self, api_key: str, api_secret: str, access_token: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self._kite = KiteConnect(api_key=api_key)
        self._instruments_cache: Optional[dict] = None
        self._instruments_fetched_at: Optional[datetime] = None
        self._lock = threading.Lock()

        if access_token:
            self.set_access_token(access_token)

    # ── Authentication ────────────────────────────────────────────────────────

    def set_access_token(self, token: str):
        self._kite.set_access_token(token)
        logger.info("Access token set.")

    def login_via_totp(self, totp_secret: str, user_id: str, password: str) -> str:
        """
        Automated Kite login using TOTP 2FA.
        Returns the access token (also sets it on the instance).

        Note: This uses the Kite web login flow via requests — requires
        your Zerodha user_id and password in addition to TOTP secret.
        """
        session = requests.Session()
        totp = pyotp.TOTP(totp_secret)

        # Step 1: Get login URL
        login_url = self._kite.login_url()
        logger.info(f"Login URL: {login_url}")

        # Step 2: POST credentials to Kite login
        resp = session.post(
            "https://kite.zerodha.com/api/login",
            data={"user_id": user_id, "password": password},
        )
        resp.raise_for_status()
        data = resp.json()
        request_id = data["data"]["request_id"]

        # Step 3: POST TOTP
        totp_resp = session.post(
            "https://kite.zerodha.com/api/twofa",
            data={
                "user_id": user_id,
                "request_id": request_id,
                "twofa_value": totp.now(),
                "twofa_type": "totp",
            },
        )
        totp_resp.raise_for_status()

        # Step 4: Exchange request_token for access_token
        # The redirect after login contains request_token as query param.
        # In automated flow, parse it from the final redirected URL.
        final_url = totp_resp.url
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(final_url).query)
        request_token = params.get("request_token", [None])[0]

        if not request_token:
            raise RuntimeError(
                "Could not extract request_token from login redirect. "
                "Check credentials and TOTP secret."
            )

        session_data = self._kite.generate_session(request_token, self.api_secret)
        access_token = session_data["access_token"]
        self.set_access_token(access_token)
        logger.info("Kite login successful via TOTP.")
        return access_token

    # ── Core API access ───────────────────────────────────────────────────────

    def get_kite(self) -> KiteConnect:
        return self._kite

    def get_ticker(self) -> KiteTicker:
        return KiteTicker(self.api_key, self._kite.access_token)

    # ── Market data helpers ───────────────────────────────────────────────────

    def get_ltp(self, instruments: list) -> dict:
        """Fetch LTP for a list of instruments like ['NSE:NIFTY 50', 'NFO:NIFTY...']."""
        return self._rate_limited(
            self._kite.ltp, _QUOTE_RATE_LIMIT, instruments
        )

    def get_quote(self, instruments: list) -> dict:
        return self._rate_limited(
            self._kite.quote, _QUOTE_RATE_LIMIT, instruments
        )

    def get_historical(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str = "5minute",
    ):
        """
        Fetch OHLCV candles. Returns list of dicts:
        [{date, open, high, low, close, volume}, ...]
        Kite returns max 60 days for intraday intervals.
        """
        import pandas as pd

        records = self._rate_limited(
            self._kite.historical_data,
            _HIST_RATE_LIMIT,
            instrument_token,
            from_date,
            to_date,
            interval,
        )
        if not records:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(records)
        df = df.rename(columns={"date": "datetime"})
        df["datetime"] = pd.to_datetime(df["datetime"])
        if df["datetime"].dt.tz is None:
            df["datetime"] = df["datetime"].dt.tz_localize(IST)
        else:
            df["datetime"] = df["datetime"].dt.tz_convert(IST)
        df = df.set_index("datetime").sort_index()
        return df[["open", "high", "low", "close", "volume"]]

    # ── Instrument lookup ─────────────────────────────────────────────────────

    def get_instruments(self, exchange: str = "NFO"):
        """
        Fetch and cache the full instrument dump for an exchange.
        Refreshes once per session (or if older than 6 hours).
        """
        import pandas as pd

        now = datetime.now(IST)
        with self._lock:
            cache_key = exchange
            if (
                self._instruments_cache is None
                or cache_key not in self._instruments_cache
                or (now - self._instruments_fetched_at).seconds > 6 * 3600
            ):
                logger.info(f"Fetching instruments for {exchange}...")
                raw = self._kite.instruments(exchange=exchange)
                if self._instruments_cache is None:
                    self._instruments_cache = {}
                self._instruments_cache[cache_key] = pd.DataFrame(raw)
                self._instruments_fetched_at = now
                logger.info(
                    f"Loaded {len(self._instruments_cache[cache_key])} instruments for {exchange}."
                )
            return self._instruments_cache[cache_key]

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(self, **kwargs) -> str:
        return self._rate_limited(self._kite.place_order, _ORDER_RATE_LIMIT, **kwargs)

    def cancel_order(self, variety: str, order_id: str):
        return self._rate_limited(
            self._kite.cancel_order, _ORDER_RATE_LIMIT, variety=variety, order_id=order_id
        )

    def orders(self) -> list:
        return self._kite.orders()

    def positions(self) -> dict:
        return self._kite.positions()

    # ── Rate-limit helper ─────────────────────────────────────────────────────

    def _rate_limited(self, fn, delay: float, *args, retries: int = 3, **kwargs):
        for attempt in range(retries):
            try:
                result = fn(*args, **kwargs)
                time.sleep(delay)
                return result
            except Exception as e:
                err = str(e)
                if "429" in err or "Too many requests" in err.lower():
                    wait = delay * (2 ** attempt)
                    logger.warning(f"Rate limited. Retrying in {wait:.1f}s...")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"API call failed after {retries} retries.")
