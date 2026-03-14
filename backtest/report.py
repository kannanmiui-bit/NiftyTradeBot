"""
backtest/report.py — Backtest performance reporting and charting.
"""

import os
from datetime import datetime
from typing import Optional

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from backtest.engine import BacktestResult, BacktestTrade
from utils.logger import get_logger

logger = get_logger(__name__)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def print_report(result: BacktestResult) -> dict:
    """Compute and print all backtest statistics. Returns stats dict."""
    trades = result.trades
    if not trades:
        print("No trades to report.")
        return {}

    total = len(trades)
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]
    win_rate = len(winners) / total * 100

    total_pnl = sum(t.pnl for t in trades)
    gross_profit = sum(t.pnl for t in winners) if winners else 0
    gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    avg_win_pct = (
        sum(t.pnl_pct for t in winners) / len(winners) * 100 if winners else 0
    )
    avg_loss_pct = (
        sum(abs(t.pnl_pct) for t in losers) / len(losers) * 100 if losers else 0
    )

    # Equity curve
    equity = [0.0]
    for t in trades:
        equity.append(equity[-1] + t.pnl)
    max_dd = _max_drawdown(equity)

    # Exit breakdown
    exit_counts = {}
    for t in trades:
        exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1

    stats = {
        "total_trades": total,
        "winners": len(winners),
        "losers": len(losers),
        "win_rate_pct": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win_pct": round(avg_win_pct, 1),
        "avg_loss_pct": round(avg_loss_pct, 1),
        "max_drawdown": round(max_dd, 2),
        "exit_breakdown": exit_counts,
    }

    print("\n" + "=" * 55)
    print("  BACKTEST REPORT")
    print("=" * 55)
    print(f"  Total Trades     : {total}")
    print(f"  Winners / Losers : {len(winners)} / {len(losers)}")
    print(f"  Win Rate         : {win_rate:.1f}%")
    print(f"  Total P&L        : Rs.{total_pnl:+,.2f}")
    print(f"  Gross Profit     : Rs.{gross_profit:,.2f}")
    print(f"  Gross Loss       : Rs.{gross_loss:,.2f}")
    print(f"  Profit Factor    : {profit_factor:.2f}")
    print(f"  Avg Win          : {avg_win_pct:.1f}%  Avg Loss: {avg_loss_pct:.1f}%")
    print(f"  Max Drawdown     : Rs.{max_dd:,.2f}")
    print(f"  Exit Breakdown   : {exit_counts}")
    print("=" * 55)

    return stats


def save_trade_log(result: BacktestResult, filename: Optional[str] = None) -> str:
    """Save all trades to a CSV file. Returns file path."""
    if not filename:
        filename = f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path = os.path.join(REPORTS_DIR, filename)
    rows = []
    for t in result.trades:
        rows.append({
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "quantity": t.quantity,
            "pnl": round(t.pnl, 2),
            "pnl_pct": round(t.pnl_pct * 100, 2),
            "exit_reason": t.exit_reason,
            "scores": str(t.signal_scores),
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    logger.info(f"Trade log saved: {path}")
    return path


def plot_results(result: BacktestResult, filename: Optional[str] = None):
    """Generate a 3-panel backtest chart and save as PNG."""
    trades = result.trades
    if not trades:
        return

    if not filename:
        filename = f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    path = os.path.join(REPORTS_DIR, filename)

    # Build equity curve
    times = [trades[0].entry_time] + [t.exit_time for t in trades]
    equity = [0.0]
    for t in trades:
        equity.append(equity[-1] + t.pnl)

    pnl_pcts = [t.pnl_pct * 100 for t in trades]
    colors = ["green" if p > 0 else "red" for p in pnl_pcts]

    fig, axes = plt.subplots(3, 1, figsize=(14, 10))
    fig.suptitle("Backtest Results — Nifty Options Bot", fontsize=14, fontweight="bold")

    # Panel 1: Equity curve
    axes[0].plot(times, equity, color="steelblue", linewidth=1.5)
    axes[0].fill_between(times, equity, alpha=0.15, color="steelblue")
    axes[0].set_title("Equity Curve (Cumulative P&L)")
    axes[0].set_ylabel("P&L (Rs.)")
    axes[0].axhline(0, color="gray", linewidth=0.8, linestyle="--")
    axes[0].xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

    # Panel 2: Per-trade P&L bars
    trade_nums = list(range(1, len(trades) + 1))
    axes[1].bar(trade_nums, pnl_pcts, color=colors, alpha=0.8, width=0.7)
    axes[1].set_title("Per-Trade P&L (%)")
    axes[1].set_ylabel("P&L %")
    axes[1].axhline(0, color="gray", linewidth=0.8, linestyle="--")
    axes[1].set_xlabel("Trade #")

    # Panel 3: Exit reason pie
    exit_counts: dict = {}
    for t in trades:
        exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1
    axes[2].pie(
        exit_counts.values(),
        labels=exit_counts.keys(),
        autopct="%1.0f%%",
        startangle=90,
        colors=["#4CAF50", "#F44336", "#FF9800", "#2196F3", "#9E9E9E"],
    )
    axes[2].set_title("Exit Reason Distribution")

    plt.tight_layout()
    plt.savefig(path, dpi=120, bbox_inches="tight")
    plt.close()
    logger.info(f"Chart saved: {path}")
    return path


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
    return max_dd
