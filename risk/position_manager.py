"""
risk/position_manager.py — Position lifecycle and trailing stop loss state machine.

The monitor() method is called every poll_seconds and implements:
  1. Hard stop loss
  2. Fixed target
  3. Peak tracking
  4. Trailing SL activation + application
  5. Time-based squareoff
"""

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, time as dtime
from typing import Optional

import pytz

from options.strike_selector import SelectedOption, SpreadLegs
from strategy.selector import SignalResult
from utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "state", "position.json"
)


@dataclass
class PositionState:
    tradingsymbol: str         # for buy: option symbol; for spread: sell leg symbol
    entry_price: float         # for buy: premium paid; for spread: net credit received
    quantity: int
    entry_time: str            # ISO string for JSON serializability
    peak_price: float
    trailing_activated: bool
    status: str                # "OPEN" | "CLOSED"
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_time: Optional[str] = None
    signal_scores: dict = field(default_factory=dict)
    option_type: str = ""
    strategy_type: str = "buy"         # "buy" | "sell_spread"
    hedge_tradingsymbol: str = ""      # spread only: OTM long (hedge) leg symbol


class PositionManager:
    def __init__(
        self,
        target_pct: float = 0.50,
        sl_pct: float = 0.30,
        trail_trigger_pct: float = 0.30,
        trail_step_pct: float = 0.20,
        squareoff_time: str = "15:10",
    ):
        self.target_pct = target_pct
        self.sl_pct = sl_pct
        self.trail_trigger_pct = trail_trigger_pct
        self.trail_step_pct = trail_step_pct
        sq_h, sq_m = map(int, squareoff_time.split(":"))
        self._squareoff_time = dtime(sq_h, sq_m)

        self._position: Optional[PositionState] = None
        self._closed_today: list[PositionState] = []

        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)

    # ── Position lifecycle ────────────────────────────────────────────────────

    def open_position(
        self,
        option: SelectedOption,
        signal: SignalResult,
        confirmed_entry_price: Optional[float] = None,
    ) -> PositionState:
        """Record a new open position. Use confirmed_entry_price if fill is known."""
        entry_price = confirmed_entry_price or option.entry_ltp
        now = datetime.now(IST)

        self._position = PositionState(
            tradingsymbol=option.tradingsymbol,
            entry_price=entry_price,
            quantity=option.quantity,
            entry_time=now.isoformat(),
            peak_price=entry_price,
            trailing_activated=False,
            status="OPEN",
            signal_scores=signal.individual_scores.copy(),
            option_type=option.option_type,
        )
        self.save_state()
        logger.info(
            f"Position opened: {option.tradingsymbol} @ {entry_price:.2f} "
            f"qty={option.quantity} | Scores={signal.individual_scores}"
        )
        return self._position

    def open_spread_position(
        self,
        spread: SpreadLegs,
        signal: SignalResult,
        confirmed_net_credit: Optional[float] = None,
    ) -> PositionState:
        """Record a new open credit spread position."""
        net_credit = confirmed_net_credit or spread.net_credit
        now = datetime.now(IST)

        self._position = PositionState(
            tradingsymbol=spread.sell_tradingsymbol,
            entry_price=net_credit,
            quantity=spread.quantity,
            entry_time=now.isoformat(),
            peak_price=net_credit,
            trailing_activated=False,
            status="OPEN",
            signal_scores=signal.individual_scores.copy(),
            option_type=spread.option_type,
            strategy_type="sell_spread",
            hedge_tradingsymbol=spread.buy_tradingsymbol,
        )
        self.save_state()
        logger.info(
            f"Spread position opened: SELL {spread.sell_tradingsymbol} | "
            f"BUY {spread.buy_tradingsymbol} | Credit={net_credit:.2f} "
            f"qty={spread.quantity}"
        )
        return self._position

    def monitor_spread(self, cost_to_close: float) -> Optional[str]:
        """
        Monitor a credit spread position.
        cost_to_close = current sell_leg_ltp - buy_leg_ltp (what it costs to close now).

        Profit when cost_to_close < entry_credit (spread narrowed).
        Loss   when cost_to_close > entry_credit (spread widened).
        """
        if self._position is None or self._position.status != "OPEN":
            return None

        pos = self._position
        entry_credit = pos.entry_price
        pnl = entry_credit - cost_to_close
        profit_pct = pnl / entry_credit if entry_credit > 0 else 0.0

        logger.debug(
            f"Spread monitor | CTC={cost_to_close:.2f} | Credit={entry_credit:.2f} | "
            f"P&L={pnl:+.2f} ({profit_pct:+.1%}) | "
            f"Trailing={'ON' if pos.trailing_activated else 'OFF'}"
        )

        # Step 1 — Hard stop loss (loss exceeds sl_pct * credit)
        if profit_pct <= -self.sl_pct:
            logger.info(f"SPREAD STOP LOSS | CTC={cost_to_close:.2f} ({profit_pct:+.1%})")
            return "STOP_LOSS"

        # Step 2 — Fixed target (captured target_pct of credit)
        if profit_pct >= self.target_pct:
            logger.info(f"SPREAD TARGET | CTC={cost_to_close:.2f} ({profit_pct:+.1%})")
            return "TARGET"

        # Step 3 — Update peak profit (lowest ctc = peak profit for sellers)
        if cost_to_close < pos.peak_price:
            pos.peak_price = cost_to_close
            self.save_state()

        peak_profit_pct = (entry_credit - pos.peak_price) / entry_credit if entry_credit > 0 else 0.0

        # Step 4 — Activate trailing SL
        if not pos.trailing_activated and peak_profit_pct >= self.trail_trigger_pct:
            pos.trailing_activated = True
            logger.info(
                f"Spread trailing SL ACTIVATED | Peak CTC={pos.peak_price:.2f} | "
                f"Peak profit={peak_profit_pct:+.1%}"
            )
            self.save_state()

        # Step 5 — Apply trailing SL (ctc risen above trail_step from peak)
        if pos.trailing_activated:
            trail_sl_ctc = pos.peak_price * (1 + self.trail_step_pct)
            if cost_to_close >= trail_sl_ctc:
                logger.info(
                    f"SPREAD TRAILING SL | CTC={cost_to_close:.2f} | "
                    f"Trail SL CTC={trail_sl_ctc:.2f}"
                )
                return "TRAILING_SL"

        # Step 6 — Time-based squareoff
        if datetime.now(IST).time() >= self._squareoff_time:
            return "SQUAREOFF"

        return None

    def monitor(self, current_ltp: float) -> Optional[str]:
        """
        Core trailing stop loss state machine.
        Call every poll_seconds with the latest LTP.
        Returns exit_reason string if an exit should be triggered, else None.

        Algorithm:
          Step 1 — Hard stop loss
          Step 2 — Fixed target
          Step 3 — Update peak
          Step 4 — Activate trailing SL
          Step 5 — Apply trailing SL
          Step 6 — Time-based squareoff
        """
        if self._position is None or self._position.status != "OPEN":
            return None

        pos = self._position
        entry = pos.entry_price
        profit_pct = (current_ltp - entry) / entry

        logger.debug(
            f"Monitor | LTP={current_ltp:.2f} | Entry={entry:.2f} | "
            f"P&L={profit_pct:+.1%} | Peak={pos.peak_price:.2f} | "
            f"Trailing={'ON' if pos.trailing_activated else 'OFF'}"
        )

        # Step 1 — Hard stop loss
        if profit_pct <= -self.sl_pct:
            logger.info(f"STOP LOSS triggered at {current_ltp:.2f} ({profit_pct:+.1%})")
            return "STOP_LOSS"

        # Step 2 — Fixed target
        if profit_pct >= self.target_pct:
            logger.info(f"TARGET hit at {current_ltp:.2f} ({profit_pct:+.1%})")
            return "TARGET"

        # Step 3 — Update peak price
        if current_ltp > pos.peak_price:
            pos.peak_price = current_ltp
            self.save_state()

        # Step 4 — Activate trailing SL
        if not pos.trailing_activated and profit_pct >= self.trail_trigger_pct:
            pos.trailing_activated = True
            trail_sl = pos.peak_price * (1 - self.trail_step_pct)
            logger.info(
                f"Trailing SL ACTIVATED | Peak={pos.peak_price:.2f} | "
                f"Trail SL={trail_sl:.2f}"
            )
            self.save_state()

        # Step 5 — Apply trailing SL
        if pos.trailing_activated:
            trail_sl_price = pos.peak_price * (1 - self.trail_step_pct)
            if current_ltp <= trail_sl_price:
                logger.info(
                    f"TRAILING SL triggered | LTP={current_ltp:.2f} | "
                    f"Trail SL={trail_sl_price:.2f} | Peak={pos.peak_price:.2f}"
                )
                return "TRAILING_SL"

        # Step 6 — Time-based force squareoff
        if datetime.now(IST).time() >= self._squareoff_time:
            logger.info("SQUAREOFF time reached.")
            return "SQUAREOFF"

        return None

    def close_position(self, reason: str, exit_price: float):
        """Mark current position as closed."""
        if self._position is None:
            return
        pos = self._position
        pos.status = "CLOSED"
        pos.exit_price = exit_price
        pos.exit_reason = reason
        pos.exit_time = datetime.now(IST).isoformat()

        pnl = (exit_price - pos.entry_price) * pos.quantity
        logger.info(
            f"Position closed: {pos.tradingsymbol} | Reason={reason} | "
            f"Exit={exit_price:.2f} | P&L={pnl:+.2f}"
        )
        self._closed_today.append(pos)
        self._position = None
        self.save_state()

    def has_open_position(self) -> bool:
        return self._position is not None and self._position.status == "OPEN"

    def current_position(self) -> Optional[PositionState]:
        return self._position

    # ── Daily P&L ─────────────────────────────────────────────────────────────

    def get_daily_pnl(self) -> float:
        total = 0.0
        for p in self._closed_today:
            if p.exit_price is not None:
                if p.strategy_type == "sell_spread":
                    # entry_price = net_credit received; exit_price = cost_to_close at exit
                    total += (p.entry_price - p.exit_price) * p.quantity
                else:
                    total += (p.exit_price - p.entry_price) * p.quantity
        return total

    def get_trade_count_today(self) -> int:
        return len(self._closed_today) + (1 if self.has_open_position() else 0)

    # ── Crash recovery ────────────────────────────────────────────────────────

    def save_state(self):
        data = {}
        if self._position:
            data["position"] = asdict(self._position)
        try:
            with open(STATE_PATH, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Failed to save position state: {e}")

    def load_state(self):
        if not os.path.exists(STATE_PATH):
            return
        try:
            with open(STATE_PATH) as f:
                data = json.load(f)
            if "position" in data:
                d = data["position"]
                self._position = PositionState(**d)
                if self._position.status == "OPEN":
                    logger.info(
                        f"Restored open position from state file: "
                        f"{self._position.tradingsymbol}"
                    )
                else:
                    self._position = None
        except Exception as e:
            logger.warning(f"Failed to load position state: {e}")
