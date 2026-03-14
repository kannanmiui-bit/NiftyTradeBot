"""
options/strike_selector.py — Converts a directional signal into a concrete NFO option.
"""

from dataclasses import dataclass
from datetime import date

from options.chain import OptionsChain
from strategy.selector import SignalResult
from broker.kite_client import KiteClient
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SelectedOption:
    tradingsymbol: str
    instrument_token: int
    strike: int
    option_type: str    # "CE" | "PE"
    expiry: date
    entry_ltp: float
    lot_size: int
    quantity: int       # lot_size * num_lots


class StrikeSelector:
    def __init__(
        self,
        chain: OptionsChain,
        kite_client: KiteClient,
        lot_size: int = 50,
        strike_step: int = 50,
        otm_strikes: int = 0,
        num_lots: int = 1,
        exchange: str = "NFO",
    ):
        self.chain = chain
        self.kite = kite_client
        self.lot_size = lot_size
        self.strike_step = strike_step
        self.otm_strikes = otm_strikes   # 0 = ATM, 1 = 1 strike OTM
        self.num_lots = num_lots
        self.exchange = exchange

    def select(self, signal: SignalResult, index_ltp: float) -> SelectedOption:
        """
        Given a bullish/bearish signal and the current index LTP,
        resolve the exact NFO option to buy.

        CE: ATM + (otm_strikes × strike_step)
        PE: ATM - (otm_strikes × strike_step)
        """
        if signal.direction not in ("CE", "PE"):
            raise ValueError(f"Cannot select option for direction={signal.direction}")

        expiry = self.chain.get_current_expiry()
        atm = self.chain.get_atm_strike(index_ltp, self.strike_step)

        if signal.direction == "CE":
            strike = atm + self.otm_strikes * self.strike_step
        else:
            strike = atm - self.otm_strikes * self.strike_step

        token, symbol = self.chain.get_option_token(strike, signal.direction, expiry)

        # Fetch current LTP of the option
        ltp_key = f"{self.exchange}:{symbol}"
        ltp_data = self.kite.get_ltp([ltp_key])
        entry_ltp = ltp_data[ltp_key]["last_price"]

        logger.info(
            f"Selected option: {symbol} | Strike={strike} | "
            f"Type={signal.direction} | LTP={entry_ltp:.2f}"
        )

        return SelectedOption(
            tradingsymbol=symbol,
            instrument_token=token,
            strike=strike,
            option_type=signal.direction,
            expiry=expiry,
            entry_ltp=entry_ltp,
            lot_size=self.lot_size,
            quantity=self.lot_size * self.num_lots,
        )
