"""
options/chain.py — NFO options chain lookup and expiry resolution.
"""

from datetime import datetime, date
from typing import Optional, Tuple

import pandas as pd
import pytz

from broker.kite_client import KiteClient
from utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class OptionsChain:
    def __init__(self, kite_client: KiteClient, index: str = "NIFTY"):
        self.kite = kite_client
        self.index = index.upper()
        self._instruments: Optional[pd.DataFrame] = None

    def _load_instruments(self) -> pd.DataFrame:
        if self._instruments is None:
            self._instruments = self.kite.get_instruments("NFO")
            # Filter to only this index's options
            self._instruments = self._instruments[
                self._instruments["name"] == self.index
            ]
            self._instruments = self._instruments[
                self._instruments["instrument_type"].isin(["CE", "PE"])
            ]
            self._instruments["expiry"] = pd.to_datetime(
                self._instruments["expiry"]
            ).dt.date
        return self._instruments

    def get_current_expiry(self) -> date:
        """Return the nearest upcoming expiry date for this index."""
        df = self._load_instruments()
        today = datetime.now(IST).date()
        future_expiries = sorted(df["expiry"].unique())
        future_expiries = [e for e in future_expiries if e >= today]
        if not future_expiries:
            raise RuntimeError("No upcoming expiry found in NFO instruments.")
        expiry = future_expiries[0]
        logger.info(f"Using expiry: {expiry}")
        return expiry

    def get_atm_strike(self, ltp: float, strike_step: int = 50) -> int:
        """Round LTP to nearest strike step."""
        return int(round(ltp / strike_step) * strike_step)

    def get_option_token(
        self,
        strike: int,
        option_type: str,
        expiry: date,
    ) -> Tuple[int, str]:
        """
        Return (instrument_token, tradingsymbol) for the given strike/type/expiry.
        Raises ValueError if not found.
        """
        df = self._load_instruments()
        filtered = df[
            (df["strike"] == float(strike))
            & (df["instrument_type"] == option_type.upper())
            & (df["expiry"] == expiry)
        ]
        if filtered.empty:
            raise ValueError(
                f"No NFO instrument found: {self.index} {strike} {option_type} exp={expiry}"
            )
        row = filtered.iloc[0]
        return int(row["instrument_token"]), str(row["tradingsymbol"])

    def get_chain_snapshot(self, expiry: date) -> pd.DataFrame:
        """Return full CE+PE chain for given expiry as a DataFrame."""
        df = self._load_instruments()
        return df[df["expiry"] == expiry].copy()
