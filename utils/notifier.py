"""
utils/notifier.py — Telegram notification helper.
Silently fails (logs warning) if Telegram is unreachable or unconfigured.
"""

import requests
from utils.logger import get_logger

logger = get_logger(__name__)


class Notifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._enabled = bool(token and chat_id)

    def send(self, message: str) -> bool:
        """Send a plain-text Telegram message. Returns True on success."""
        if not self._enabled:
            logger.debug("Telegram not configured — skipping notification.")
            return False
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            resp = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Telegram notification failed: {e}")
            return False

    def alert_entry(self, symbol: str, entry_price: float, signal_scores: dict, direction: str):
        lines = [
            f"<b>ENTRY {direction}</b>",
            f"Symbol : {symbol}",
            f"Price  : {entry_price:.2f}",
            "Scores : " + ", ".join(f"{k}={v:+d}" for k, v in signal_scores.items()),
        ]
        self.send("\n".join(lines))

    def alert_exit(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        reason: str,
        quantity: int,
    ):
        pnl = (exit_price - entry_price) * quantity
        pnl_pct = (exit_price - entry_price) / entry_price * 100
        lines = [
            f"<b>EXIT — {reason}</b>",
            f"Symbol : {symbol}",
            f"Entry  : {entry_price:.2f}  Exit: {exit_price:.2f}",
            f"P&amp;L   : {pnl:+.2f} ({pnl_pct:+.1f}%)",
        ]
        self.send("\n".join(lines))

    def alert_error(self, message: str):
        self.send(f"<b>ERROR</b>\n{message}")
