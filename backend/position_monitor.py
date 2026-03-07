"""
Position Monitor — auto-exit open BUY positions every minute.

Rules (checked in priority order):
  1. Force exit at 1:25 PM IST — liquidity drops near market close
  2. Profit target hit (₹300–₹500 by default) — lock in gain
  3. Extended profit (pnl > target_max) — exit immediately, don't let winner reverse
  4. Stop-loss breach (per-order sl_percentage, default 3%) — cut loss fast

Auto-entry remains fully manual. Only exits are automated.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from database import SessionLocal
from kite_service import KiteService
from models import Order

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
PROFIT_TARGET_MIN = float(os.getenv("PROFIT_TARGET_MIN", 300))
PROFIT_TARGET_MAX = float(os.getenv("PROFIT_TARGET_MAX", 500))
DEFAULT_SL_PCT    = float(os.getenv("STOP_LOSS_PERCENTAGE", 3))   # per-order SL fallback
POLL_INTERVAL_SEC = 60

# Force-exit all positions at 1:25 PM — 5 min before Kite auto-squares off
FORCE_EXIT_HOUR   = 13
FORCE_EXIT_MINUTE = 25

# ── Per-user running state ────────────────────────────────────────────────────
# user_id → True (running) / False (stop requested)
_active_monitors: Dict[int, bool] = {}


# ── Exit decision logic ───────────────────────────────────────────────────────

def _is_force_exit_time() -> bool:
    now = datetime.now()
    return now.hour > FORCE_EXIT_HOUR or (
        now.hour == FORCE_EXIT_HOUR and now.minute >= FORCE_EXIT_MINUTE
    )


def _check_exit(order: Order, current_price: float) -> Tuple[bool, str]:
    """
    Return (should_exit, reason_string).
    Checks in priority order: close-time → profit → extended profit → stop-loss.
    """
    if order.entry_price is None:
        return False, "No entry price recorded"

    pnl      = (current_price - order.entry_price) * order.quantity
    pnl_pct  = (current_price - order.entry_price) / order.entry_price * 100
    sl_pct   = order.sl_percentage if order.sl_percentage else DEFAULT_SL_PCT

    # 1. Force exit — market about to close
    if _is_force_exit_time():
        return True, f"FORCE EXIT at market close | P&L ₹{pnl:+.2f}"

    # 2. Profit target window
    if PROFIT_TARGET_MIN <= pnl <= PROFIT_TARGET_MAX:
        return True, f"PROFIT TARGET | ₹{pnl:+.2f} in target range ₹{PROFIT_TARGET_MIN}–{PROFIT_TARGET_MAX}"

    # 3. Extended profit — exit before reversal
    if pnl > PROFIT_TARGET_MAX:
        return True, f"EXTENDED PROFIT | ₹{pnl:+.2f} exceeds max target — locking in"

    # 4. Stop-loss breach
    if pnl_pct <= -sl_pct:
        return True, f"STOP LOSS | {pnl_pct:.2f}% drop (limit -{sl_pct}%) | ₹{pnl:+.2f}"

    return False, f"HOLDING | P&L ₹{pnl:+.2f} ({pnl_pct:+.2f}%) | SL at -{sl_pct}%"


# ── Core monitor class ────────────────────────────────────────────────────────

class PositionMonitor:
    def __init__(self, access_token: str, db):
        self.kite = KiteService(access_token)
        self.db   = db

    def run_once(self, user_id: int) -> List[Dict]:
        """
        One monitoring cycle:
          - Fetch all open BUY positions for user
          - Get LTP in a single batch call
          - Exit any that trigger the rules above
        Returns list of exit event dicts for logging/response.
        """
        open_orders = (
            self.db.query(Order)
            .filter(
                Order.user_id    == user_id,
                Order.order_type == "BUY",
                Order.status     == "EXECUTED",
                Order.exit_price.is_(None),
            )
            .all()
        )

        if not open_orders:
            logger.info(f"[Monitor] user={user_id} — no open positions")
            return []

        # Batch LTP — single API call for all open instruments
        symbols  = list({f"NFO:{o.instrument}" for o in open_orders})
        ltp_map  = self.kite.get_ltp(symbols)

        exits = []
        for order in open_orders:
            key           = f"NFO:{order.instrument}"
            current_price = ltp_map.get(key)

            if current_price is None:
                logger.warning(f"[Monitor] LTP unavailable for {order.instrument} — skipping")
                continue

            should_exit, reason = _check_exit(order, current_price)
            pnl = (current_price - order.entry_price) * order.quantity if order.entry_price else 0

            logger.info(
                f"[Monitor] {order.instrument} | entry=₹{order.entry_price:.2f} "
                f"ltp=₹{current_price:.2f} pnl=₹{pnl:+.2f} | {reason}"
            )

            if not should_exit:
                continue

            # ── Place SELL ────────────────────────────────────────────────
            sell_broker_id = self.kite.place_order(
                tradingsymbol=order.instrument,
                transaction_type="SELL",
                quantity=order.quantity,
                order_type="MARKET",
            )

            if sell_broker_id is None:
                logger.error(f"[Monitor] SELL failed for {order.instrument} — will retry next cycle")
                continue

            # Cancel the linked SL-M order (Kite won't allow both to coexist)
            if order.sl_broker_order_id:
                cancelled = self.kite.cancel_order(order.sl_broker_order_id)
                if cancelled:
                    logger.info(f"[Monitor] SL order {order.sl_broker_order_id} cancelled")

            # Record sell in DB
            sell_record = Order(
                user_id        = user_id,
                instrument     = order.instrument,
                quantity       = order.quantity,
                price          = current_price,
                order_type     = "SELL",
                status         = "EXECUTED",
                broker_order_id= sell_broker_id,
                strategy_id    = order.strategy_id,
                exit_price     = current_price,
                profit_loss    = pnl,
                executed_at    = datetime.now(),
            )
            order.exit_price  = current_price
            order.profit_loss = pnl

            self.db.add(sell_record)
            self.db.commit()

            event = {
                "instrument"  : order.instrument,
                "reason"      : reason,
                "entry_price" : order.entry_price,
                "exit_price"  : current_price,
                "quantity"    : order.quantity,
                "profit_loss" : round(pnl, 2),
                "exited_at"   : datetime.now().isoformat(),
            }
            exits.append(event)
            logger.info(
                f"[Monitor] ✓ EXIT {order.instrument} @ ₹{current_price:.2f} "
                f"P&L=₹{pnl:+.2f} | {reason}"
            )

        return exits


# ── Async loop ────────────────────────────────────────────────────────────────

async def _monitoring_loop(user_id: int, access_token: str):
    logger.info(f"[Monitor] Started for user {user_id} — polling every {POLL_INTERVAL_SEC}s")
    _active_monitors[user_id] = True

    while _active_monitors.get(user_id, False):
        db = SessionLocal()
        try:
            monitor = PositionMonitor(access_token=access_token, db=db)
            exits   = monitor.run_once(user_id)
            if exits:
                logger.info(f"[Monitor] {len(exits)} position(s) exited this cycle for user {user_id}")
        except Exception as e:
            logger.error(f"[Monitor] Unhandled error for user {user_id}: {e}")
        finally:
            db.close()

        # Wait the full interval before next check, but honour stop requests
        for _ in range(POLL_INTERVAL_SEC):
            if not _active_monitors.get(user_id, False):
                break
            await asyncio.sleep(1)

    _active_monitors.pop(user_id, None)
    logger.info(f"[Monitor] Stopped for user {user_id}")


# ── Public control API ────────────────────────────────────────────────────────

def start_monitor(user_id: int, access_token: str) -> bool:
    """Launch the background monitoring loop. Returns False if already running."""
    if _active_monitors.get(user_id):
        return False
    asyncio.create_task(_monitoring_loop(user_id, access_token))
    return True


def stop_monitor(user_id: int) -> bool:
    """Signal the running loop to stop. Returns False if not running."""
    if not _active_monitors.get(user_id):
        return False
    _active_monitors[user_id] = False
    return True


def is_running(user_id: int) -> bool:
    return bool(_active_monitors.get(user_id))


def get_all_active() -> List[int]:
    return [uid for uid, running in _active_monitors.items() if running]
