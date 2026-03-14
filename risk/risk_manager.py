"""
risk/risk_manager.py — Pre-trade gate checks before any order is placed.
"""

from datetime import datetime, time as dtime
from typing import Tuple

import pytz

from risk.position_manager import PositionManager
from utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class RiskManager:
    def __init__(
        self,
        position_manager: PositionManager,
        max_trades_per_day: int = 2,
        max_daily_loss: float = -5000.0,
        no_new_trade_after: str = "14:00",
    ):
        self.positions = position_manager
        self.max_trades = max_trades_per_day
        self.max_daily_loss = max_daily_loss
        nt_h, nt_m = map(int, no_new_trade_after.split(":"))
        self._no_new_trade_after = dtime(nt_h, nt_m)

    def can_place_trade(self) -> Tuple[bool, str]:
        """
        Run all pre-trade gates in order.
        Returns (True, "OK") or (False, reason_string).
        """
        # Gate 1: Already have an open position
        if self.positions.has_open_position():
            pos = self.positions.current_position()
            return False, f"Position already open: {pos.tradingsymbol}"

        # Gate 2: Max trades per day reached
        count = self.positions.get_trade_count_today()
        if count >= self.max_trades:
            return False, f"Max trades/day reached ({count}/{self.max_trades})"

        # Gate 3: Daily loss limit breached
        daily_pnl = self.positions.get_daily_pnl()
        if daily_pnl <= self.max_daily_loss:
            return False, f"Daily loss limit reached (P&L={daily_pnl:.2f})"

        # Gate 4: Past no-new-trade cutoff
        now_time = datetime.now(IST).time()
        if now_time >= self._no_new_trade_after:
            return False, f"Past no-new-trade cutoff ({self._no_new_trade_after})"

        return True, "OK"

    def log_risk_status(self):
        """Log current risk metrics."""
        daily_pnl = self.positions.get_daily_pnl()
        trade_count = self.positions.get_trade_count_today()
        logger.info(
            f"Risk Status | Daily P&L={daily_pnl:+.2f} | "
            f"Trades today={trade_count}/{self.max_trades}"
        )
