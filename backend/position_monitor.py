"""
Position Monitor — DB-driven, auto-resuming, alert-writing position watcher.

Design:
  - MonitoringSession table is the source of truth (survives restarts).
  - In-memory set (_running_tasks) prevents duplicate task spawning.
  - Stop signal: asyncio.Event per user — stop_monitor() sets it, loop wakes
    immediately instead of polling the DB 60 times per minute.
  - Every cycle: batch LTP → check 4 exit rules → write PositionAlert to DB.
  - Actionable alerts auto-execute the exit and mark themselves actioned.
  - WATCHING alerts (every WATCHING_EVERY_N cycles) are informational only.
  - On server startup and on login, DB is queried to resume active sessions.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, Optional, Set, Tuple

from sqlalchemy.orm import Session, joinedload

from alert_hub import hub
from database import SessionLocal
from kite_service import KiteService
from models import AlertType, MonitoringSession, Order, PositionAlert

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
# Both profit and stop-loss are now percentage-based (symmetric ±3%).
PROFIT_TARGET_PCT  = float(os.getenv("PROFIT_TARGET_PCT", 3))   # exit on +3%
DEFAULT_SL_PCT     = float(os.getenv("STOP_LOSS_PERCENTAGE", 3)) # exit on -3%
POLL_INTERVAL_SEC  = 60
WATCHING_EVERY_N   = 5   # write a WATCHING alert every N cycles (~5 min)

FORCE_EXIT_HOUR    = 13
FORCE_EXIT_MINUTE  = 25

# In-memory state — DB is truth for persistence, these are runtime-only.
_running_tasks: Set[int] = set()
_stop_events:  Dict[int, asyncio.Event] = {}


# ── Exit decision logic ───────────────────────────────────────────────────────

def _is_force_exit_time() -> bool:
    now = datetime.now()
    return now.hour > FORCE_EXIT_HOUR or (
        now.hour == FORCE_EXIT_HOUR and now.minute >= FORCE_EXIT_MINUTE
    )


def _check_exit(
    order: Order, current_price: float
) -> Tuple[bool, str, str, Optional[float], Optional[float]]:
    """
    Returns (should_exit, alert_type, message, pnl, pnl_pct).
    pnl/pnl_pct are None when entry_price is absent.
    Priority: close-time → profit target → extended profit → stop-loss.
    """
    if order.entry_price is None:
        return False, AlertType.WATCHING, "No entry price — cannot evaluate", None, None

    pnl     = (current_price - order.entry_price) * order.quantity
    pnl_pct = (current_price - order.entry_price) / order.entry_price * 100
    sl_pct  = order.sl_percentage if order.sl_percentage else DEFAULT_SL_PCT

    pnl_r = round(pnl, 2)
    pct_r = round(pnl_pct, 2)

    profit_pct = PROFIT_TARGET_PCT

    if _is_force_exit_time():
        return True, AlertType.FORCE_EXIT, f"Market closing — force exit | P&L ₹{pnl:+.2f} ({pct_r:+.2f}%)", pnl_r, pct_r

    if pnl_pct >= profit_pct:
        return True, AlertType.PROFIT_TARGET, (
            f"🎯 Profit target hit +{pnl_pct:.2f}% (target +{profit_pct}%) | ₹{pnl:+.2f} — exiting"
        ), pnl_r, pct_r

    if pnl_pct <= -sl_pct:
        return True, AlertType.STOP_LOSS, (
            f"🛑 Stop-loss hit {pnl_pct:.2f}% (limit -{sl_pct}%) | ₹{pnl:+.2f} — cutting loss"
        ), pnl_r, pct_r

    return False, AlertType.WATCHING, (
        f"Holding | LTP ₹{current_price:.2f} | P&L ₹{pnl:+.2f} ({pnl_pct:+.2f}%) | SL at -{sl_pct}%"
    ), pnl_r, pct_r


# ── Shared sell helper ────────────────────────────────────────────────────────

def execute_sell_and_record(
    kite: KiteService,
    db: Session,
    order: Order,
    user_id: int,
    current_price: float,
) -> Tuple[Optional[str], float, Optional[Order]]:
    """
    Place SELL MARKET, cancel SL, record P&L on the buy order, add a SELL
    Order row to the session.  Does NOT commit — caller decides.

    Returns (sell_broker_id, pnl, sell_record).
    Returns (None, 0, None) if Kite order placement fails.
    """
    sell_broker_id = kite.place_order(
        tradingsymbol    = order.instrument,
        transaction_type = "SELL",
        quantity         = order.quantity,
        order_type       = "MARKET",
    )
    if not sell_broker_id:
        return None, 0, None

    if order.sl_broker_order_id:
        kite.cancel_order(order.sl_broker_order_id)

    pnl = (current_price - order.entry_price) * order.quantity if order.entry_price else 0

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
    db.add(sell_record)
    return sell_broker_id, pnl, sell_record


# ── Alert writer ──────────────────────────────────────────────────────────────

def _write_alert(
    db: Session,
    user_id: int,
    order: Order,
    alert_type: str,
    message: str,
    current_price: float,
    pnl: Optional[float] = None,
    pnl_pct: Optional[float] = None,
    is_actioned: bool = False,
) -> PositionAlert:
    """Write a PositionAlert row. Accepts pre-computed pnl/pnl_pct to avoid
    recalculating values the caller already has."""
    alert = PositionAlert(
        user_id       = user_id,
        order_id      = order.id,
        instrument    = order.instrument,
        alert_type    = alert_type,
        current_price = current_price,
        entry_price   = order.entry_price,
        pnl           = pnl,
        pnl_pct       = pnl_pct,
        message       = message,
        is_actioned   = is_actioned,
    )
    db.add(alert)
    db.flush()
    return alert


# ── Core monitor ──────────────────────────────────────────────────────────────

class PositionMonitor:
    def __init__(self, access_token: str, db: Session):
        self.kite = KiteService(access_token)
        self.db   = db

    def execute_exit(
        self,
        order: Order,
        current_price: float,
        user_id: int,
        reason: str,
        alert_type: str,
        pnl: Optional[float],
        pnl_pct: Optional[float],
    ) -> bool:
        """Sell, record, write actioned alert, commit."""
        sell_broker_id, pnl_actual, _ = execute_sell_and_record(
            self.kite, self.db, order, user_id, current_price
        )
        if not sell_broker_id:
            logger.error(f"[Monitor] SELL failed for {order.instrument} — will retry next cycle")
            return False

        _write_alert(
            db            = self.db,
            user_id       = user_id,
            order         = order,
            alert_type    = alert_type,
            message       = reason,
            current_price = current_price,
            pnl           = pnl,
            pnl_pct       = pnl_pct,
            is_actioned   = True,
        )
        self.db.commit()
        logger.info(f"[Monitor] ✓ EXIT {order.instrument} @ ₹{current_price:.2f} P&L=₹{pnl_actual:+.2f} | {reason}")

        # Push real-time alert to any connected WebSocket clients
        hub.broadcast(user_id, {
            "type":          "ALERT",
            "alert_type":    alert_type,
            "instrument":    order.instrument,
            "current_price": current_price,
            "entry_price":   order.entry_price,
            "pnl":           round(pnl_actual, 2),
            "pnl_pct":       round(pnl_pct, 2) if pnl_pct is not None else None,
            "message":       reason,
            "actioned":      True,
        })
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

        symbols      = list({f"NFO:{o.instrument}" for o in open_orders})
        ltp_map      = self.kite.get_ltp(symbols)
        write_watching = (cycle_num % WATCHING_EVERY_N == 0)

        for order in open_orders:
            key           = f"NFO:{order.instrument}"
            current_price = ltp_map.get(key)

            if current_price is None:
                logger.warning(f"[Monitor] LTP unavailable for {order.instrument}")
                continue

            should_exit, alert_type, message, pnl, pnl_pct = _check_exit(order, current_price)
            logger.info(f"[Monitor] {order.instrument} | {message}")

            if should_exit:
                self.execute_exit(order, current_price, user_id, message, alert_type, pnl, pnl_pct)
            elif write_watching:
                _write_alert(
                    db            = self.db,
                    user_id       = user_id,
                    order         = order,
                    alert_type    = AlertType.WATCHING,
                    message       = message,
                    current_price = current_price,
                    pnl           = pnl,
                    pnl_pct       = pnl_pct,
                )
                self.db.commit()
                # Push live position update to WebSocket
                hub.broadcast(user_id, {
                    "type":          "WATCHING",
                    "instrument":    order.instrument,
                    "current_price": current_price,
                    "entry_price":   order.entry_price,
                    "pnl":           round(pnl, 2) if pnl is not None else None,
                    "pnl_pct":       round(pnl_pct, 2) if pnl_pct is not None else None,
                    "message":       message,
                })


# ── Async loop ────────────────────────────────────────────────────────────────

async def _monitoring_loop(user_id: int, access_token: str):
    """
    Main monitoring loop. Uses asyncio.Event for instant stop signalling —
    stop_monitor() sets the event and the sleep wakes up immediately instead
    of polling the DB 60 times per minute.
    """
    logger.info(f"[Monitor] Loop started for user {user_id}")
    stop_event = asyncio.Event()
    _stop_events[user_id] = stop_event
    _running_tasks.add(user_id)
    cycle = 0

    try:
        while not stop_event.is_set():
            db = SessionLocal()
            try:
                session = db.query(MonitoringSession).filter(
                    MonitoringSession.user_id == user_id
                ).first()

                if not session or not session.is_active:
                    logger.info(f"[Monitor] DB stop signal for user {user_id}")
                    break

                session.last_heartbeat = datetime.now()
                db.commit()

                PositionMonitor(access_token=access_token, db=db).run_once(
                    user_id=user_id, cycle_num=cycle
                )
                cycle += 1

            except Exception as e:
                logger.error(f"[Monitor] Error for user {user_id}: {e}")
            finally:
                db.close()

            # Sleep for POLL_INTERVAL_SEC but wake instantly if stop_event is set.
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass   # normal timeout — proceed to next cycle

    finally:
        _running_tasks.discard(user_id)
        _stop_events.pop(user_id, None)
        logger.info(f"[Monitor] Loop stopped for user {user_id}")


# ── Public API ────────────────────────────────────────────────────────────────

def start_monitor(user_id: int, access_token: str, db: Session) -> bool:
    """Persist active session to DB and launch asyncio task. Returns False if already running."""
    if user_id in _running_tasks:
        return False

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
    """Flip DB flag and signal event — loop stops within milliseconds."""
    session = db.query(MonitoringSession).filter(
        MonitoringSession.user_id == user_id
    ).first()
    if not session or not session.is_active:
        return False
    session.is_active = False
    db.commit()

    event = _stop_events.get(user_id)
    if event:
        event.set()
    return True


def is_running(user_id: int) -> bool:
    return user_id in _running_tasks


def resume_if_active(user_id: int, access_token: str, db: Session) -> bool:
    """
    Resume monitoring ONLY if an active MonitoringSession already exists in DB.
    Does NOT create a new session. Safe to call on every login.
    """
    if user_id in _running_tasks:
        return False
    session = db.query(MonitoringSession).filter(
        MonitoringSession.user_id   == user_id,
        MonitoringSession.is_active == True,
    ).first()
    if not session:
        return False
    asyncio.create_task(_monitoring_loop(user_id, access_token))
    return True


def resume_all_from_db(db: Session) -> int:
    """
    Called on server startup. Restarts monitoring for all users with an active
    MonitoringSession. Returns count of resumed sessions.
    Uses joinedload to avoid N+1 queries.
    """
    active = (
        db.query(MonitoringSession)
        .options(joinedload(MonitoringSession.user))
        .filter(MonitoringSession.is_active == True)
        .all()
    )

    count = 0
    for ms in active:
        user = ms.user
        if user and user.access_token and ms.user_id not in _running_tasks:
            asyncio.create_task(_monitoring_loop(ms.user_id, user.access_token))
            count += 1
            logger.info(f"[Monitor] Resumed for user {ms.user_id} on startup")

    return count
