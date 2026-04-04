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


@dataclass
class SpreadLegs:
    """A credit spread: one short (sell) leg + one long (hedge) leg."""
    sell_tradingsymbol: str
    sell_token: int
    buy_tradingsymbol: str
    buy_token: int
    sell_strike: int
    buy_strike: int
    option_type: str    # "CE" | "PE" (both legs use the same type)
    signal_direction: str  # original signal "CE" | "PE"
    expiry: date
    sell_ltp: float
    buy_ltp: float
    net_credit: float   # sell_ltp - buy_ltp (received per unit)
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
        hedge_otm_strikes: int = 2,
    ):
        self.chain = chain
        self.kite = kite_client
        self.lot_size = lot_size
        self.strike_step = strike_step
        self.otm_strikes = otm_strikes   # 0 = ATM, 1 = 1 strike OTM
        self.num_lots = num_lots
        self.exchange = exchange
        self.hedge_otm_strikes = hedge_otm_strikes

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

    def select_spread(self, signal: SignalResult, index_ltp: float) -> SpreadLegs:
        """
        Build a credit spread from a directional signal.

        CE (bullish)  → Bull Put Spread: sell ATM PE, buy OTM PE (hedge below)
        PE (bearish)  → Bear Call Spread: sell ATM CE, buy OTM CE (hedge above)
        """
        if signal.direction not in ("CE", "PE"):
            raise ValueError(f"Cannot select spread for direction={signal.direction}")

        # Spread trades the OPPOSITE option type to the signal direction
        opt_type = "PE" if signal.direction == "CE" else "CE"

        expiry = self.chain.get_current_expiry()
        atm = self.chain.get_atm_strike(index_ltp, self.strike_step)

        # Sell leg: ATM + otm_strikes above ATM (e.g. otm_strikes=1 → ATM+50)
        sell_strike = atm + self.otm_strikes * self.strike_step
        # Buy leg: hedge_otm_strikes × step below/above sell strike (not ATM)
        if opt_type == "PE":
            buy_strike = sell_strike - self.hedge_otm_strikes * self.strike_step
        else:
            buy_strike = sell_strike + self.hedge_otm_strikes * self.strike_step

        sell_token, sell_symbol = self.chain.get_option_token(sell_strike, opt_type, expiry)
        buy_token, buy_symbol = self.chain.get_option_token(buy_strike, opt_type, expiry)

        ltp_data = self.kite.get_ltp([
            f"{self.exchange}:{sell_symbol}",
            f"{self.exchange}:{buy_symbol}",
        ])
        sell_ltp = ltp_data[f"{self.exchange}:{sell_symbol}"]["last_price"]
        buy_ltp  = ltp_data[f"{self.exchange}:{buy_symbol}"]["last_price"]
        net_credit = round(sell_ltp - buy_ltp, 2)

        logger.info(
            f"Spread selected: SELL {sell_symbol} @ {sell_ltp:.2f} | "
            f"BUY {buy_symbol} @ {buy_ltp:.2f} | Credit={net_credit:.2f}"
        )

        return SpreadLegs(
            sell_tradingsymbol=sell_symbol,
            sell_token=sell_token,
            buy_tradingsymbol=buy_symbol,
            buy_token=buy_token,
            sell_strike=sell_strike,
            buy_strike=buy_strike,
            option_type=opt_type,
            signal_direction=signal.direction,
            expiry=expiry,
            sell_ltp=sell_ltp,
            buy_ltp=buy_ltp,
            net_credit=net_credit,
            lot_size=self.lot_size,
            quantity=self.lot_size * self.num_lots,
        )
