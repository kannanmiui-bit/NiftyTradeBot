"""
main.py — Nifty/BankNifty Options Buying Bot — Main agent entry point.

Two concurrent loops:
  Loop 1 (Signal)  — fires every candle interval, evaluates strategies, places orders
  Loop 2 (Monitor) — fires every poll_seconds, manages open positions with SL/Target/Trail

Run:
    python main.py [--paper]   # --paper disables actual order placement (dry run)
"""

import argparse
import sys
import time
from datetime import datetime, time as dtime

import pytz
import schedule

from config import load_config
from broker.kite_client import KiteClient
from broker.order_manager import OrderManager
from data.market_data import MarketDataManager
from data.indicators import IndicatorEngine
from strategy.selector import StrategySelector
from options.chain import OptionsChain
from options.strike_selector import StrikeSelector
from risk.position_manager import PositionManager
from risk.risk_manager import RiskManager
from utils.logger import setup_logger, get_logger
from utils.notifier import Notifier

IST = pytz.timezone("Asia/Kolkata")

# Zerodha instrument tokens (update these to your actual index tokens)
INDEX_TOKENS = {
    "NIFTY": 256265,       # NSE:NIFTY 50
    "BANKNIFTY": 260105,   # NSE:NIFTY BANK
}
INDEX_LTP_KEYS = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
}

CANDLE_INTERVAL_MINUTES = {
    "3minute": 3, "5minute": 5, "10minute": 10, "15minute": 15,
}


def is_market_hours(config) -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:   # Saturday / Sunday
        return False
    open_h, open_m = map(int, config.market_open.split(":"))
    close_h, close_m = map(int, config.market_close.split(":"))
    open_t = dtime(open_h, open_m)
    close_t = dtime(close_h, close_m)
    return open_t <= now.time() <= close_t


def main():
    parser = argparse.ArgumentParser(description="Nifty Options Bot")
    parser.add_argument(
        "--paper", action="store_true",
        help="Paper mode: log signals and risk checks but do NOT place real orders"
    )
    args = parser.parse_args()
    paper_mode = args.paper

    # ── Setup ────────────────────────────────────────────────────────────────
    setup_logger()
    logger = get_logger("main")
    config = load_config()

    logger.info("=" * 60)
    logger.info(f"  Nifty Options Bot starting | Index={config.index}")
    logger.info(f"  Mode={'PAPER' if paper_mode else 'LIVE'}")
    logger.info(f"  Strategy: {config.strategy_type} ({config.signal_type}) | Threshold={config.score_threshold}")
    logger.info(
        f"  Risk: Target={config.target_pct:.0%} | SL={config.sl_pct:.0%} | "
        f"Trail trigger={config.trail_trigger_pct:.0%} | Trail step={config.trail_step_pct:.0%}"
    )
    logger.info("=" * 60)

    notifier = Notifier(config.telegram_token, config.telegram_chat_id)

    # ── Broker ───────────────────────────────────────────────────────────────
    kite = KiteClient(config.api_key, config.api_secret, config.access_token)

    if not config.access_token:
        logger.error(
            "KITE_ACCESS_TOKEN not set. "
            "Run the login helper or set it in .env after daily Kite login."
        )
        sys.exit(1)

    # ── Components ───────────────────────────────────────────────────────────
    order_mgr = OrderManager(kite)
    market_data = MarketDataManager(kite, config.candle_interval)
    indicator_engine = IndicatorEngine(
        config.supertrend_period, config.supertrend_mult,
        config.rsi_period, config.ema_fast, config.ema_slow,
    )
    selector = StrategySelector(
        config.score_threshold,
        config.supertrend_period, config.supertrend_mult,
        config.rsi_period, config.rsi_overbought, config.rsi_oversold,
        config.ema_fast, config.ema_slow,
        config.orb_end, config.vwap_band_pct,
    )
    chain = OptionsChain(kite, config.index)
    strike_sel = StrikeSelector(
        chain, kite, config.lot_size, config.strike_step,
        config.otm_strikes, config.num_lots, config.exchange,
        config.hedge_otm_strikes,
    )
    position_mgr = PositionManager(
        config.target_pct, config.sl_pct,
        config.trail_trigger_pct, config.trail_step_pct,
        config.squareoff_time,
    )
    risk_mgr = RiskManager(
        position_mgr, config.max_trades_per_day,
        config.max_daily_loss, config.no_new_trade_after,
    )

    # ── Startup: crash recovery + warm-up ────────────────────────────────────
    position_mgr.load_state()

    instrument_token = INDEX_TOKENS.get(config.index)
    if not instrument_token:
        logger.error(f"Unknown index: {config.index}")
        sys.exit(1)

    logger.info("Loading historical candles for indicator warm-up...")
    market_data.load_historical_candles(instrument_token, days_back=5)

    logger.info("Starting WebSocket streaming...")
    market_data.start_streaming([instrument_token])

    time.sleep(3)  # brief pause for WS connection to establish

    # ── Loop 1: Signal check (per candle) ────────────────────────────────────
    def signal_check_job():
        if not is_market_hours(config):
            return

        logger.info("--- Signal check ---")
        risk_mgr.log_risk_status()

        try:
            df = market_data.get_current_candles(instrument_token)
            if df.empty or len(df) < 30:
                logger.warning("Insufficient candle data for signal evaluation.")
                return

            # Compute ORB levels
            orb = market_data.compute_orb_levels(
                instrument_token, config.orb_start, config.orb_end
            )
            orb_high, orb_low = orb if orb else (None, None)

            # Compute indicators
            enriched_df = indicator_engine.compute_all(df, orb_high, orb_low)

            # Evaluate strategies
            signal = selector.evaluate(enriched_df)

            if signal.direction is None:
                logger.info("No signal — skipping trade.")
                return

            # Risk gates
            allowed, reason = risk_mgr.can_place_trade()
            if not allowed:
                logger.info(f"Trade blocked by risk: {reason}")
                return

            # Select option / spread
            index_ltp_key = INDEX_LTP_KEYS[config.index]
            index_ltp_data = kite.get_ltp([index_ltp_key])
            index_ltp = index_ltp_data[index_ltp_key]["last_price"]

            if config.strategy_type == "sell_spread":
                try:
                    spread = strike_sel.select_spread(signal, index_ltp)
                except Exception as e:
                    logger.error(f"Spread selection failed: {e}")
                    notifier.alert_error(f"Spread selection failed: {e}")
                    return
                notifier.alert_entry(
                    f"SPREAD {spread.sell_tradingsymbol}/{spread.buy_tradingsymbol}",
                    spread.net_credit, signal.individual_scores, signal.direction
                )
                if paper_mode:
                    logger.info(
                        f"[PAPER] Would SELL {spread.sell_tradingsymbol} / "
                        f"BUY {spread.buy_tradingsymbol} | Credit={spread.net_credit:.2f}"
                    )
                    position_mgr.open_spread_position(spread, signal)
                    return
                sell_id, buy_id = order_mgr.place_spread_open(
                    spread.sell_tradingsymbol, spread.buy_tradingsymbol, spread.quantity
                )
                logger.info(f"Spread orders placed: sell={sell_id} hedge={buy_id}")
                position_mgr.open_spread_position(spread, signal)
            else:
                try:
                    option = strike_sel.select(signal, index_ltp)
                except Exception as e:
                    logger.error(f"Option selection failed: {e}")
                    notifier.alert_error(f"Option selection failed: {e}")
                    return
                notifier.alert_entry(
                    option.tradingsymbol, option.entry_ltp,
                    signal.individual_scores, signal.direction
                )
                if paper_mode:
                    logger.info(
                        f"[PAPER] Would BUY {option.tradingsymbol} "
                        f"@ {option.entry_ltp:.2f} qty={option.quantity}"
                    )
                    position_mgr.open_position(option, signal)
                    return
                order_id = order_mgr.place_buy_order(option.tradingsymbol, option.quantity)
                logger.info(f"Order placed: {order_id}")
                time.sleep(1)
                order = order_mgr.get_order_status(order_id)
                fill_price = (
                    order.get("average_price") or option.entry_ltp
                    if order else option.entry_ltp
                )
                position_mgr.open_position(option, signal, confirmed_entry_price=fill_price)

        except Exception as e:
            logger.exception(f"Error in signal_check_job: {e}")
            notifier.alert_error(str(e))

    # ── Loop 2: Position monitor (every poll_seconds) ─────────────────────────
    def position_monitor_job():
        if not position_mgr.has_open_position():
            return

        pos = position_mgr.current_position()
        try:
            if pos.strategy_type == "sell_spread":
                # Fetch both legs, compute cost-to-close
                sell_key  = f"NFO:{pos.tradingsymbol}"
                hedge_key = f"NFO:{pos.hedge_tradingsymbol}"
                ltp_data = kite.get_ltp([sell_key, hedge_key])
                sell_ltp  = ltp_data[sell_key]["last_price"]
                hedge_ltp = ltp_data[hedge_key]["last_price"]
                cost_to_close = sell_ltp - hedge_ltp
                exit_reason = position_mgr.monitor_spread(cost_to_close)
                exit_price_for_log = cost_to_close
            else:
                ltp_key = f"NFO:{pos.tradingsymbol}"
                ltp_data = kite.get_ltp([ltp_key])
                current_ltp = ltp_data[ltp_key]["last_price"]
                exit_reason = position_mgr.monitor(current_ltp)
                exit_price_for_log = current_ltp

            if exit_reason is None:
                return

            # Exit triggered
            if paper_mode:
                logger.info(f"[PAPER] EXIT {exit_reason} | {pos.tradingsymbol} @ {exit_price_for_log:.2f}")
            else:
                if pos.strategy_type == "sell_spread":
                    order_mgr.place_spread_close(
                        pos.tradingsymbol, pos.hedge_tradingsymbol,
                        pos.quantity, exit_reason
                    )
                else:
                    order_mgr.place_exit_order(pos.tradingsymbol, pos.quantity, exit_reason)
                order_mgr.log_trade(
                    tradingsymbol=pos.tradingsymbol,
                    entry_price=pos.entry_price,
                    exit_price=exit_price_for_log,
                    quantity=pos.quantity,
                    entry_time=datetime.fromisoformat(pos.entry_time),
                    exit_time=datetime.now(IST),
                    reason=exit_reason,
                    signal_scores=pos.signal_scores,
                )

            position_mgr.close_position(exit_reason, exit_price_for_log)
            notifier.alert_exit(
                pos.tradingsymbol, pos.entry_price,
                exit_price_for_log, exit_reason, pos.quantity
            )

        except Exception as e:
            logger.exception(f"Error in position_monitor_job: {e}")
            notifier.alert_error(f"Monitor error: {e}")

    # ── Force squareoff at market close ───────────────────────────────────────
    def squareoff_all():
        if position_mgr.has_open_position():
            logger.info("Squareoff time: force-closing open position.")
            pos = position_mgr.current_position()
            try:
                if pos.strategy_type == "sell_spread":
                    sell_key  = f"NFO:{pos.tradingsymbol}"
                    hedge_key = f"NFO:{pos.hedge_tradingsymbol}"
                    ltp_data = kite.get_ltp([sell_key, hedge_key])
                    exit_price = ltp_data[sell_key]["last_price"] - ltp_data[hedge_key]["last_price"]
                else:
                    ltp_key = f"NFO:{pos.tradingsymbol}"
                    ltp_data = kite.get_ltp([ltp_key])
                    exit_price = ltp_data[ltp_key]["last_price"]
            except Exception:
                exit_price = pos.entry_price  # fallback

            if not paper_mode:
                if pos.strategy_type == "sell_spread":
                    order_mgr.place_spread_close(
                        pos.tradingsymbol, pos.hedge_tradingsymbol,
                        pos.quantity, "SQUAREOFF"
                    )
                else:
                    order_mgr.place_exit_order(pos.tradingsymbol, pos.quantity, "SQUAREOFF")
            position_mgr.close_position("SQUAREOFF", exit_price)
            notifier.alert_exit(
                pos.tradingsymbol, pos.entry_price,
                exit_price, "SQUAREOFF", pos.quantity
            )

    # ── Schedule jobs ────────────────────────────────────────────────────────
    interval_mins = CANDLE_INTERVAL_MINUTES.get(config.candle_interval, 5)

    # Align signal checks to candle closes (09:15, 09:20, 09:25 ...) rather
    # than firing from bot start time.  We schedule one job per aligned minute
    # slot within the hour (e.g. :00, :05, :10, ... :55 for 5-minute candles).
    aligned_slots = []
    for m in range(0, 60, interval_mins):
        slot = f":{m:02d}"
        schedule.every().hour.at(slot).do(signal_check_job)
        aligned_slots.append(slot)

    schedule.every(config.poll_seconds).seconds.do(position_monitor_job)
    schedule.every().day.at(config.squareoff_time).do(squareoff_all)

    notifier.send(
        f"Bot started | Index={config.index} | "
        f"Mode={'PAPER' if paper_mode else 'LIVE'}"
    )
    logger.info(
        f"Scheduler running | Signal at {aligned_slots} each hour | "
        f"Monitor every {config.poll_seconds}s | Squareoff at {config.squareoff_time}"
    )

    # ── Main run loop ─────────────────────────────────────────────────────────
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
        squareoff_all()
        market_data.stop_streaming()
        notifier.send("Bot stopped.")


if __name__ == "__main__":
    main()
