"""
broker/order_manager.py — Place, track, and exit orders via Kite.
"""

import csv
import os
from datetime import datetime
from typing import Optional

import pytz
from kiteconnect import KiteConnect

from broker.kite_client import KiteClient
from utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

TRADE_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "logs", "trades.csv"
)


class OrderManager:
    def __init__(self, kite_client: KiteClient):
        self.kite = kite_client
        self._ensure_trade_log()

    def place_buy_order(
        self,
        tradingsymbol: str,
        quantity: int,
        exchange: str = "NFO",
        order_type: str = "MARKET",
        tag: str = "options_bot",
    ) -> str:
        """Place an intraday MIS BUY order. Returns order_id."""
        logger.info(f"Placing BUY order: {tradingsymbol} qty={quantity}")
        order_id = self.kite.place_order(
            variety=KiteConnect.VARIETY_REGULAR,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            transaction_type=KiteConnect.TRANSACTION_TYPE_BUY,
            quantity=quantity,
            product=KiteConnect.PRODUCT_MIS,
            order_type=KiteConnect.ORDER_TYPE_MARKET,
            tag=tag,
        )
        logger.info(f"BUY order placed: order_id={order_id}")
        return str(order_id)

    def place_sell_order(
        self,
        tradingsymbol: str,
        quantity: int,
        exchange: str = "NFO",
        tag: str = "options_bot",
    ) -> str:
        """Place an intraday MIS SELL order (for option writing). Returns order_id."""
        logger.info(f"Placing SELL (write) order: {tradingsymbol} qty={quantity}")
        order_id = self.kite.place_order(
            variety=KiteConnect.VARIETY_REGULAR,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            transaction_type=KiteConnect.TRANSACTION_TYPE_SELL,
            quantity=quantity,
            product=KiteConnect.PRODUCT_MIS,
            order_type=KiteConnect.ORDER_TYPE_MARKET,
            tag=tag,
        )
        logger.info(f"SELL (write) order placed: order_id={order_id}")
        return str(order_id)

    def place_spread_open(
        self,
        sell_symbol: str,
        buy_symbol: str,
        quantity: int,
        exchange: str = "NFO",
    ) -> tuple[str, str]:
        """Open a credit spread: sell one leg, buy the hedge leg."""
        sell_id = self.place_sell_order(sell_symbol, quantity, exchange)
        buy_id  = self.place_buy_order(buy_symbol, quantity, exchange)
        logger.info(f"Spread opened: sell={sell_id} hedge={buy_id}")
        return sell_id, buy_id

    def place_spread_close(
        self,
        sell_symbol: str,
        buy_symbol: str,
        quantity: int,
        reason: str,
        exchange: str = "NFO",
    ) -> tuple[str, str]:
        """Close a credit spread: buy back sold leg, sell the hedge leg."""
        buy_back_id  = self.place_buy_order(sell_symbol, quantity, exchange)
        sell_back_id = self.place_exit_order(buy_symbol, quantity, reason, exchange)
        logger.info(f"Spread closed ({reason}): buy_back={buy_back_id} sell_hedge={sell_back_id}")
        return buy_back_id, sell_back_id

    def place_exit_order(
        self,
        tradingsymbol: str,
        quantity: int,
        reason: str,
        exchange: str = "NFO",
        tag: str = "options_bot",
    ) -> str:
        """Place an intraday MIS SELL (exit) order. Returns order_id."""
        logger.info(f"Placing EXIT order: {tradingsymbol} qty={quantity} reason={reason}")
        order_id = self.kite.place_order(
            variety=KiteConnect.VARIETY_REGULAR,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            transaction_type=KiteConnect.TRANSACTION_TYPE_SELL,
            quantity=quantity,
            product=KiteConnect.PRODUCT_MIS,
            order_type=KiteConnect.ORDER_TYPE_MARKET,
            tag=f"{tag}_{reason}",
        )
        logger.info(f"EXIT order placed: order_id={order_id} reason={reason}")
        return str(order_id)

    def get_order_status(self, order_id: str) -> Optional[dict]:
        """Return the order dict for a given order_id, or None if not found."""
        try:
            orders = self.kite.orders()
            for o in orders:
                if str(o["order_id"]) == str(order_id):
                    return o
        except Exception as e:
            logger.warning(f"Could not fetch order status: {e}")
        return None

    def get_open_positions(self, tag: str = "options_bot") -> list:
        """Return all open intraday positions placed by this bot."""
        try:
            pos = self.kite.positions()
            day_pos = pos.get("day", [])
            return [
                p for p in day_pos
                if p.get("product") == "MIS" and p.get("quantity", 0) != 0
            ]
        except Exception as e:
            logger.warning(f"Could not fetch positions: {e}")
            return []

    def log_trade(
        self,
        tradingsymbol: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        entry_time: datetime,
        exit_time: datetime,
        reason: str,
        signal_scores: dict,
    ):
        """Append a completed trade to the CSV trade log."""
        pnl = (exit_price - entry_price) * quantity
        row = {
            "date": entry_time.strftime("%Y-%m-%d"),
            "symbol": tradingsymbol,
            "entry_time": entry_time.strftime("%H:%M:%S"),
            "exit_time": exit_time.strftime("%H:%M:%S"),
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "quantity": quantity,
            "pnl": round(pnl, 2),
            "exit_reason": reason,
            "scores": str(signal_scores),
        }
        with open(TRADE_LOG_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            writer.writerow(row)
        logger.info(
            f"Trade logged: {tradingsymbol} | {reason} | P&L={pnl:+.2f}"
        )

    def _ensure_trade_log(self):
        if not os.path.exists(TRADE_LOG_PATH):
            with open(TRADE_LOG_PATH, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "date", "symbol", "entry_time", "exit_time",
                    "entry_price", "exit_price", "quantity",
                    "pnl", "exit_reason", "scores",
                ])
