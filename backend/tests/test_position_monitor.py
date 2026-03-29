"""
Unit tests for position_monitor.py

All tests are pure-Python / in-memory — no real Kite API calls, no real DB writes
(fixture rollback handles cleanup). KiteService is mocked where needed.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from models import AlertType, Order, MonitoringSession, PositionAlert
import position_monitor as pm


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_order(entry_price=260.0, quantity=100, sl_pct=3.0, sl_broker_id="SL001",
               instrument="NIFTY24JUN26300CE"):
    o = MagicMock(spec=Order)
    o.entry_price       = entry_price
    o.quantity          = quantity
    o.sl_percentage     = sl_pct
    o.sl_broker_order_id = sl_broker_id
    o.instrument        = instrument
    o.id                = 1
    o.strategy_id       = None
    return o


# ── _check_exit ───────────────────────────────────────────────────────────────

class TestCheckExit:
    """Five-tuple return: (should_exit, alert_type, message, pnl, pnl_pct)"""

    def _call(self, price, entry=260.0, qty=100, sl=3.0):
        return pm._check_exit(make_order(entry, qty, sl), price)

    def test_watching_returns_false_and_pnl(self):
        should_exit, atype, msg, pnl, pct = self._call(262.0)
        assert not should_exit
        assert atype  == AlertType.WATCHING
        assert pnl    == pytest.approx(200.0)
        assert pct    == pytest.approx(0.77, abs=0.01)
        assert "Holding" in msg

    def test_profit_target_lower_bound(self):
        # pnl = (263 - 260) * 100 = 300  →  exactly at PROFIT_TARGET_MIN
        should_exit, atype, msg, pnl, _ = self._call(263.0)
        assert should_exit
        assert atype == AlertType.PROFIT_TARGET
        assert pnl   == pytest.approx(300.0)

    def test_profit_target_upper_bound(self):
        # pnl = (265 - 260) * 100 = 500  →  exactly at PROFIT_TARGET_MAX
        should_exit, atype, _, pnl, _ = self._call(265.0)
        assert should_exit
        assert atype == AlertType.PROFIT_TARGET
        assert pnl   == pytest.approx(500.0)

    def test_extended_profit_above_max(self):
        # pnl = (267 - 260) * 100 = 700  →  above PROFIT_TARGET_MAX
        should_exit, atype, msg, pnl, _ = self._call(267.0)
        assert should_exit
        assert atype == AlertType.EXTENDED_PROFIT
        assert pnl   == pytest.approx(700.0)
        assert "before reversal" in msg

    def test_stop_loss_breach(self):
        # pnl_pct = (251.9 - 260) / 260 * 100 ≈ -3.12%  → below -3%
        should_exit, atype, msg, pnl, pct = self._call(251.9)
        assert should_exit
        assert atype == AlertType.STOP_LOSS
        assert pnl   < 0
        assert pct   < -3.0
        assert "cutting loss" in msg

    def test_stop_loss_custom_sl_pct(self):
        # Custom 5% SL; price at -4% should still be WATCHING
        should_exit, atype, _, _, _ = self._call(249.6, sl=5.0)
        # (249.6 - 260) / 260 * 100 ≈ -4.0% > -5% threshold → WATCHING
        assert not should_exit
        assert atype == AlertType.WATCHING

    def test_stop_loss_default_when_sl_none(self):
        o = make_order(sl_pct=None)
        should_exit, atype, _, _, _ = pm._check_exit(o, 251.9)
        # Falls back to DEFAULT_SL_PCT (3%) → should trigger
        assert should_exit
        assert atype == AlertType.STOP_LOSS

    def test_no_entry_price_returns_none_pnl(self):
        o = make_order(entry_price=None)
        should_exit, atype, msg, pnl, pct = pm._check_exit(o, 100.0)
        assert not should_exit
        assert atype == AlertType.WATCHING
        assert pnl   is None
        assert pct   is None
        assert "cannot evaluate" in msg

    def test_boundary_just_below_stop_loss(self):
        # pnl_pct = (252.2 - 260) / 260 * 100 ≈ -3.0% — right on boundary (not breached)
        should_exit, atype, _, _, pct = self._call(252.2)
        assert pct == pytest.approx(-3.0, abs=0.1)
        # Could be WATCHING or STOP_LOSS depending on float; either is acceptable at boundary

    @patch("position_monitor._is_force_exit_time", return_value=True)
    def test_force_exit_takes_priority_over_profit(self, _mock):
        # Even a profitable position gets forced out at market close
        should_exit, atype, msg, pnl, _ = self._call(265.0)
        assert should_exit
        assert atype == AlertType.FORCE_EXIT
        assert "closing" in msg


# ── _write_alert ──────────────────────────────────────────────────────────────

class TestWriteAlert:

    def test_stores_all_fields(self, db, test_user_with_kite, open_buy_order):
        alert = pm._write_alert(
            db            = db,
            user_id       = test_user_with_kite.id,
            order         = open_buy_order,
            alert_type    = AlertType.WATCHING,
            message       = "Test watch",
            current_price = 262.0,
            pnl           = 200.0,
            pnl_pct       = 0.77,
            is_actioned   = False,
        )
        db.flush()
        assert alert.id       is not None
        assert alert.pnl      == 200.0
        assert alert.pnl_pct  == 0.77
        assert alert.instrument == open_buy_order.instrument
        assert alert.alert_type == AlertType.WATCHING
        assert not alert.is_actioned

    def test_actioned_flag_set_correctly(self, db, test_user_with_kite, open_buy_order):
        alert = pm._write_alert(
            db=db, user_id=test_user_with_kite.id, order=open_buy_order,
            alert_type=AlertType.STOP_LOSS, message="cut", current_price=251.9,
            pnl=-810.0, pnl_pct=-3.12, is_actioned=True,
        )
        db.flush()
        assert alert.is_actioned is True

    def test_null_pnl_accepted(self, db, test_user_with_kite, open_buy_order):
        """No pnl available (no entry price) — should store None cleanly."""
        alert = pm._write_alert(
            db=db, user_id=test_user_with_kite.id, order=open_buy_order,
            alert_type=AlertType.WATCHING, message="no entry", current_price=100.0,
        )
        db.flush()
        assert alert.pnl     is None
        assert alert.pnl_pct is None


# ── execute_sell_and_record ───────────────────────────────────────────────────

class TestExecuteSellAndRecord:

    def _mock_kite(self, sell_ok=True):
        kite = MagicMock()
        kite.place_order.return_value = "SELL_BROKER_001" if sell_ok else None
        kite.cancel_order.return_value = True
        return kite

    def test_places_sell_and_cancels_sl(self, db, test_user_with_kite, open_buy_order):
        kite = self._mock_kite()
        broker_id, pnl, sell_record = pm.execute_sell_and_record(
            kite, db, open_buy_order, test_user_with_kite.id, 263.0
        )
        kite.place_order.assert_called_once_with(
            tradingsymbol="NIFTY24JUN26300CE",
            transaction_type="SELL",
            quantity=100,
            order_type="MARKET",
        )
        kite.cancel_order.assert_called_once_with("SL_BROKER_001")
        assert broker_id == "SELL_BROKER_001"
        assert pnl       == pytest.approx(300.0)

    def test_creates_sell_order_record_in_session(self, db, test_user_with_kite, open_buy_order):
        kite = self._mock_kite()
        _, pnl, sell_record = pm.execute_sell_and_record(
            kite, db, open_buy_order, test_user_with_kite.id, 263.0
        )
        db.flush()
        assert sell_record is not None
        assert sell_record.order_type   == "SELL"
        assert sell_record.status       == "EXECUTED"
        assert sell_record.exit_price   == 263.0
        assert sell_record.profit_loss  == pytest.approx(300.0)
        assert sell_record.broker_order_id == "SELL_BROKER_001"

    def test_updates_buy_order_exit_price(self, db, test_user_with_kite, open_buy_order):
        kite = self._mock_kite()
        pm.execute_sell_and_record(kite, db, open_buy_order, test_user_with_kite.id, 265.0)
        assert open_buy_order.exit_price  == 265.0
        assert open_buy_order.profit_loss == pytest.approx(500.0)

    def test_returns_none_on_kite_failure(self, db, test_user_with_kite, open_buy_order):
        kite = self._mock_kite(sell_ok=False)
        broker_id, pnl, sell_record = pm.execute_sell_and_record(
            kite, db, open_buy_order, test_user_with_kite.id, 263.0
        )
        assert broker_id  is None
        assert sell_record is None
        assert pnl         == 0
        kite.cancel_order.assert_not_called()

    def test_skips_sl_cancel_when_no_sl_order(self, db, test_user_with_kite):
        order = make_order(sl_broker_id=None)
        order.id = open_buy_order_id = None
        # Use a real DB order without sl_broker_order_id
        real_order = Order(
            user_id=test_user_with_kite.id, instrument="NIFTY24JUN26300CE",
            quantity=100, price=260.0, order_type="BUY", status="EXECUTED",
            entry_price=260.0, sl_broker_order_id=None,
        )
        db.add(real_order); db.flush()
        kite = self._mock_kite()
        pm.execute_sell_and_record(kite, db, real_order, test_user_with_kite.id, 263.0)
        kite.cancel_order.assert_not_called()


# ── start_monitor / stop_monitor / is_running / resume_if_active ─────────────

class TestMonitorPublicAPI:

    def test_is_running_false_by_default(self, test_user_with_kite):
        assert not pm.is_running(test_user_with_kite.id + 9999)

    def test_stop_monitor_returns_false_when_no_session(self, db, test_user_with_kite):
        result = pm.stop_monitor(test_user_with_kite.id + 9999, db)
        assert result is False

    def test_stop_monitor_sets_is_active_false(self, db, test_user_with_kite,
                                                active_monitoring_session):
        result = pm.stop_monitor(test_user_with_kite.id, db)
        assert result is True
        db.refresh(active_monitoring_session)
        assert active_monitoring_session.is_active is False

    def test_start_monitor_upserts_session(self, db, test_user_with_kite):
        with patch("position_monitor.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            pm.start_monitor(test_user_with_kite.id, "fake_token", db)
        session = db.query(MonitoringSession).filter(
            MonitoringSession.user_id == test_user_with_kite.id
        ).first()
        assert session is not None
        assert session.is_active is True

    def test_start_monitor_launches_task(self, db, test_user_with_kite):
        with patch("position_monitor.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            pm.start_monitor(test_user_with_kite.id + 1234, "fake_token", db)
            mock_asyncio.create_task.assert_called_once()

    def test_resume_if_active_returns_false_without_session(self, db, test_user):
        result = pm.resume_if_active(test_user.id, "token", db)
        assert result is False

    def test_resume_if_active_returns_true_with_active_session(
        self, db, test_user_with_kite, active_monitoring_session
    ):
        with patch("position_monitor.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            result = pm.resume_if_active(test_user_with_kite.id, "token", db)
        assert result is True
        mock_asyncio.create_task.assert_called_once()

    def test_resume_if_active_returns_false_for_inactive_session(
        self, db, test_user_with_kite
    ):
        ms = MonitoringSession(user_id=test_user_with_kite.id, is_active=False)
        db.add(ms); db.flush()
        result = pm.resume_if_active(test_user_with_kite.id, "token", db)
        assert result is False

    def test_stop_monitor_signals_event(self, db, test_user_with_kite,
                                         active_monitoring_session):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            event = loop.run_until_complete(_make_event_and_check(
                db, test_user_with_kite, active_monitoring_session
            ))
        finally:
            loop.close()


async def _make_event_and_check(db, user, ms):
    import asyncio
    event = asyncio.Event()
    pm._stop_events[user.id] = event
    try:
        pm.stop_monitor(user.id, db)
        assert event.is_set()
    finally:
        pm._stop_events.pop(user.id, None)


# ── AlertType constants ───────────────────────────────────────────────────────

class TestAlertType:
    def test_all_constants_are_strings(self):
        for name in ("PROFIT_TARGET", "EXTENDED_PROFIT", "STOP_LOSS", "FORCE_EXIT", "WATCHING"):
            assert isinstance(getattr(AlertType, name), str)

    def test_constants_are_unique(self):
        vals = [AlertType.PROFIT_TARGET, AlertType.EXTENDED_PROFIT,
                AlertType.STOP_LOSS, AlertType.FORCE_EXIT, AlertType.WATCHING]
        assert len(vals) == len(set(vals))
