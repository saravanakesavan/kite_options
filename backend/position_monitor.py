"""
Position Monitor — DB-driven, auto-resuming, alert-writing position watcher.

Design:
  - MonitoringSession table is the source of truth (survives restarts).
  - In-memory set tracks which tasks are currently running (avoids duplicates).
  - Every cycle: batch LTP → check 4 exit rules → write PositionAlert to DB.
  - Actionable alerts (PROFIT_TARGET, STOP_LOSS, etc.) auto-execute the exit
    and mark themselves actioned. WATCHING alerts are informational only.
  - On server startup and on login, DB is queried to resume active sessions.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional, Set, Tuple

from sqlalchemy.orm import Session

from database import SessionLocal
from kite_service import KiteService
from models import MonitoringSession, Order, PositionAlert

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
PROFIT_TARGET_MIN  = float(os.getenv("PROFIT_TARGET_MIN", 300))
PROFIT_TARGET_MAX  = float(os.getenv("PROFIT_TARGET_MAX", 500))
DEFAULT_SL_PCT     = float(os.getenv("STOP_LOSS_PERCENTAGE", 3))
POLL_INTERVAL_SEC  = 60
WATCHING_EVERY_N   = 5   # write a WATCHING alert every N cycles (~5 min)

FORCE_EXIT_HOUR    = 13
FORCE_EXIT_MINUTE  = 25

# In-memory set of user_ids whose asyncio task is currently running.
# DB is truth for persistence; this set prevents duplicate task spawning.
_running_tasks: Set[int] = set()


# ── Exit decision logic ───────────────────────────────────────────────────────

def _is_force_exit_time() -> bool:
    now = datetime.now()
    return now.hour > FORCE_EXIT_HOUR or (
        now.hour == FORCE_EXIT_HOUR and now.minute >= FORCE_EXIT_MINUTE
    )


def _check_exit(order: Order, current_price: float) -> Tuple[bool, str, str]:
    """
    Returns (should_exit, alert_type, message).
    Priority: close-time → profit → extended profit → stop-loss.
    """
    if order.entry_price is None:
        return False, "WATCHING", "No entry price — cannot evaluate"

    pnl     = (current_price - order.entry_price) * order.quantity
    pnl_pct = (current_price - order.entry_price) / order.entry_price * 100
    sl_pct  = order.sl_percentage if order.sl_percentage else DEFAULT_SL_PCT

    if _is_force_exit_time():
        return True, "FORCE_EXIT", f"Market closing — force exit | P&L ₹{pnl:+.2f}"

    if PROFIT_TARGET_MIN <= pnl <= PROFIT_TARGET_MAX:
        return True, "PROFIT_TARGET", f"Profit target hit ₹{pnl:+.2f} (range ₹{PROFIT_TARGET_MIN}–{PROFIT_TARGET_MAX}) — exiting"

    if pnl > PROFIT_TARGET_MAX:
        return True, "EXTENDED_PROFIT", f"Extended profit ₹{pnl:+.2f} — exiting before reversal"

    if pnl_pct <= -sl_pct:
        return True, "STOP_LOSS", f"Stop-loss breached {pnl_pct:.2f}% (limit -{sl_pct}%) | ₹{pnl:+.2f} — cutting loss"

    return False, "WATCHING", f"Holding | LTP ₹{current_price:.2f} | P&L ₹{pnl:+.2f} ({pnl_pct:+.2f}%) | SL at -{sl_pct}%"


# ── Alert writer ──────────────────────────────────────────────────────────────

def _write_alert(
    db: Session,
    user_id: int,
    order: Order,
    alert_type: str,
    message: str,
    current_price: float,
    is_actioned: bool = False,
) -> PositionAlert:
    pnl = (current_price - order.entry_price) * order.quantity if order.entry_price else None
    pnl_pct = (current_price - order.entry_price) / order.entry_price * 100 if order.entry_price else None

    alert = PositionAlert(
        user_id       = user_id,
        order_id      = order.id,
        instrument    = order.instrument,
        alert_type    = alert_type,
        current_price = current_price,
        entry_price   = order.entry_price,
        pnl           = round(pnl, 2) if pnl is not None else None,
        pnl_pct       = round(pnl_pct, 2) if pnl_pct is not None else None,
        message       = message,
        is_actioned   = is_actioned,
    )
    db.add(alert)
    db.flush()   # get id without full commit
    return alert


# ── Core monitor ──────────────────────────────────────────────────────────────

class PositionMonitor:
    def __init__(self, access_token: str, db: Session):
        self.kite = KiteService(access_token)
        self.db   = db

    def execute_exit(self, order: Order, current_price: float, user_id: int, reason: str, alert_type: str):
        """Place SELL + cancel SL + update DB + mark alert actioned."""
        sell_broker_id = self.kite.place_order(
            tradingsymbol    = order.instrument,
            transaction_type = "SELL",
            quantity         = order.quantity,
            order_type       = "MARKET",
        )
        if sell_broker_id is None:
            logger.error(f"[Monitor] SELL failed for {order.instrument} — will retry next cycle")
            return False

        # Cancel linked SL-M order
        if order.sl_broker_order_id:
            self.kite.cancel_order(order.sl_broker_order_id)

        pnl = (current_price - order.entry_price) * order.quantity if order.entry_price else 0

        # Record sell order
        sell_record = Order(
            user_id         = user_id,
            instrument      = order.instrument,
            quantity        = order.quantity,
            price           = current_price,
            order_type      = "SELL",
            status          = "EXECUTED",
            broker_order_id = sell_broker_id,
            strategy_id     = order.strategy_id,
            exit_price      = current_price,
            profit_loss     = pnl,
            executed_at     = datetime.now(),
        )
        order.exit_price  = current_price
        order.profit_loss = pnl
        self.db.add(sell_record)

        # Write actioned alert
        _write_alert(
            db            = self.db,
            user_id       = user_id,
            order         = order,
            alert_type    = alert_type,
            message       = reason,
            current_price = current_price,
            is_actioned   = True,
        )
        self.db.commit()

        logger.info(f"[Monitor] ✓ EXIT {order.instrument} @ ₹{current_price:.2f} P&L=₹{pnl:+.2f} | {reason}")
        return True

    def run_once(self, user_id: int, cycle_num: int) -> None:
        """One monitoring cycle for all open positions of a user."""
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
            return

        # Single batch LTP call for all instruments
        symbols = list({f"NFO:{o.instrument}" for o in open_orders})
        ltp_map = self.kite.get_ltp(symbols)

        write_watching = (cycle_num % WATCHING_EVERY_N == 0)

        for order in open_orders:
            key           = f"NFO:{order.instrument}"
            current_price = ltp_map.get(key)

            if current_price is None:
                logger.warning(f"[Monitor] LTP unavailable for {order.instrument}")
                continue

            should_exit, alert_type, message = _check_exit(order, current_price)

            logger.info(f"[Monitor] {order.instrument} | {message}")

            if should_exit:
                self.execute_exit(order, current_price, user_id, message, alert_type)
            elif write_watching:
                # Periodic WATCHING alert so user sees status without polling
                _write_alert(
                    db            = self.db,
                    user_id       = user_id,
                    order         = order,
                    alert_type    = "WATCHING",
                    message       = message,
                    current_price = current_price,
                    is_actioned   = False,
                )
                self.db.commit()


# ── Async loop ────────────────────────────────────────────────────────────────

async def _monitoring_loop(user_id: int, access_token: str):
    logger.info(f"[Monitor] Loop started for user {user_id}")
    _running_tasks.add(user_id)
    cycle = 0

    try:
        while True:
            # Check DB for stop signal
            db = SessionLocal()
            try:
                session = db.query(MonitoringSession).filter(
                    MonitoringSession.user_id == user_id
                ).first()

                if not session or not session.is_active:
                    logger.info(f"[Monitor] DB stop signal received for user {user_id}")
                    break

                # Update heartbeat
                session.last_heartbeat = datetime.now()
                db.commit()

                monitor = PositionMonitor(access_token=access_token, db=db)
                monitor.run_once(user_id=user_id, cycle_num=cycle)
                cycle += 1

            except Exception as e:
                logger.error(f"[Monitor] Error for user {user_id}: {e}")
            finally:
                db.close()

            # 1-second granular sleep — honours stop within 1s
            for _ in range(POLL_INTERVAL_SEC):
                db = SessionLocal()
                try:
                    s = db.query(MonitoringSession).filter(
                        MonitoringSession.user_id == user_id,
                        MonitoringSession.is_active == True,
                    ).first()
                    if not s:
                        break
                finally:
                    db.close()
                await asyncio.sleep(1)
            else:
                continue
            break   # inner loop broke early → stop signal
    finally:
        _running_tasks.discard(user_id)
        logger.info(f"[Monitor] Loop stopped for user {user_id}")


# ── Public API ────────────────────────────────────────────────────────────────

def start_monitor(user_id: int, access_token: str, db: Session) -> bool:
    """
    Persist active session to DB and launch asyncio task.
    Returns False if already running.
    """
    if user_id in _running_tasks:
        return False

    # Upsert MonitoringSession
    session = db.query(MonitoringSession).filter(
        MonitoringSession.user_id == user_id
    ).first()
    if session:
        session.is_active  = True
        session.started_at = datetime.now()
    else:
        session = MonitoringSession(user_id=user_id, is_active=True)
        db.add(session)
    db.commit()

    asyncio.create_task(_monitoring_loop(user_id, access_token))
    return True


def stop_monitor(user_id: int, db: Session) -> bool:
    """Set DB flag to stop. Loop exits within 1 second."""
    session = db.query(MonitoringSession).filter(
        MonitoringSession.user_id == user_id
    ).first()
    if not session or not session.is_active:
        return False
    session.is_active = False
    db.commit()
    return True


def is_running(user_id: int) -> bool:
    return user_id in _running_tasks


def resume_all_from_db(db: Session) -> int:
    """
    Called on server startup. Restarts monitoring for all users with
    an active MonitoringSession. Returns count of resumed sessions.
    """
    from models import User
    active = db.query(MonitoringSession).filter(
        MonitoringSession.is_active == True
    ).all()

    count = 0
    for ms in active:
        user = db.query(User).filter(User.id == ms.user_id).first()
        if user and user.access_token and ms.user_id not in _running_tasks:
            asyncio.create_task(_monitoring_loop(ms.user_id, user.access_token))
            count += 1
            logger.info(f"[Monitor] Resumed for user {ms.user_id} on startup")

    return count
