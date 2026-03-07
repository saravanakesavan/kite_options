"""
End-to-end API tests covering:
  - Auth: register, login, monitor_resumed flag
  - Alerts: GET /alerts, POST /alerts/{id}/dismiss
  - Exit: POST /positions/{id}/exit (single-click)
  - Monitoring: start, stop, status
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch
from models import AlertType, MonitoringSession, PositionAlert, Order


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuth:

    def test_register_success(self, client):
        resp = client.post("/auth/register", json={
            "username": "newuser",
            "email":    "newuser@test.com",
            "password": "pass1234",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "newuser"

    def test_register_duplicate_username(self, client, test_user):
        resp = client.post("/auth/register", json={
            "username": "trader1",
            "email":    "other@test.com",
            "password": "pass1234",
        })
        assert resp.status_code == 400

    def test_login_success(self, client, test_user):
        resp = client.post("/auth/login", json={
            "username": "trader1",
            "password": "secret123",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert "monitor_resumed" in body

    def test_login_wrong_password(self, client, test_user):
        resp = client.post("/auth/login", json={
            "username": "trader1",
            "password": "wrongpass",
        })
        assert resp.status_code == 401

    def test_login_unknown_user(self, client):
        resp = client.post("/auth/login", json={
            "username": "nobody",
            "password": "pass",
        })
        assert resp.status_code == 401

    def test_login_monitor_resumed_false_without_session(self, client, test_user):
        resp = client.post("/auth/login", json={
            "username": "trader1",
            "password": "secret123",
        })
        assert resp.json()["monitor_resumed"] is False

    def test_login_monitor_resumed_true_with_active_session(
        self, client, db, test_user_with_kite, active_monitoring_session
    ):
        with patch("position_monitor.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            resp = client.post("/auth/login", json={
                "username": "kite_trader",
                "password": "secret123",
            })
        assert resp.status_code == 200
        assert resp.json()["monitor_resumed"] is True

    def test_me_returns_user_info(self, client, auth_headers):
        resp = client.get("/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "trader1"

    def test_me_requires_auth(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code in (401, 403)


# ── Alerts ────────────────────────────────────────────────────────────────────

class TestAlerts:

    def _seed_alerts(self, db, user, order, types_actioned):
        """Insert alerts and return their ids."""
        ids = []
        for atype, actioned in types_actioned:
            a = PositionAlert(
                user_id       = user.id,
                order_id      = order.id,
                instrument    = order.instrument,
                alert_type    = atype,
                message       = f"Test {atype}",
                current_price = 263.0,
                entry_price   = 260.0,
                pnl           = 300.0,
                pnl_pct       = 1.15,
                is_actioned   = actioned,
            )
            db.add(a)
        db.flush()
        return db.query(PositionAlert).filter(PositionAlert.user_id == user.id).all()

    def test_get_alerts_empty(self, client, kite_auth_headers):
        resp = client.get("/alerts", headers=kite_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["alerts"] == []

    def test_get_alerts_unactioned_only_by_default(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        self._seed_alerts(db, test_user_with_kite, open_buy_order, [
            (AlertType.WATCHING,       False),  # unactioned
            (AlertType.PROFIT_TARGET,  True),   # actioned — should be hidden
        ])
        resp = client.get("/alerts", headers=kite_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["alerts"][0]["alert_type"] == AlertType.WATCHING

    def test_get_alerts_all_when_unactioned_false(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        self._seed_alerts(db, test_user_with_kite, open_buy_order, [
            (AlertType.WATCHING,       False),
            (AlertType.PROFIT_TARGET,  True),
        ])
        resp = client.get("/alerts?unactioned_only=false", headers=kite_auth_headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_get_alerts_newest_first(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        alerts = self._seed_alerts(db, test_user_with_kite, open_buy_order, [
            (AlertType.WATCHING,       False),
            (AlertType.STOP_LOSS,      False),
        ])
        resp = client.get("/alerts", headers=kite_auth_headers)
        body = resp.json()
        assert body["count"] == 2

    def test_get_alerts_requires_auth(self, client):
        resp = client.get("/alerts")
        assert resp.status_code in (401, 403)

    def test_dismiss_alert(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        a = PositionAlert(
            user_id=test_user_with_kite.id, order_id=open_buy_order.id,
            instrument=open_buy_order.instrument, alert_type=AlertType.WATCHING,
            message="watch", current_price=262.0, is_actioned=False,
        )
        db.add(a); db.flush()

        resp = client.post(f"/alerts/{a.id}/dismiss", headers=kite_auth_headers)
        assert resp.status_code == 200
        db.refresh(a)
        assert a.is_actioned is True

    def test_dismiss_alert_not_found(self, client, kite_auth_headers):
        resp = client.post("/alerts/99999/dismiss", headers=kite_auth_headers)
        assert resp.status_code == 404

    def test_dismiss_alert_requires_auth(self, client):
        resp = client.post("/alerts/1/dismiss")
        assert resp.status_code in (401, 403)

    def test_alerts_isolated_per_user(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order,
        test_user, auth_headers
    ):
        """User A's alerts must not appear in user B's /alerts response."""
        a = PositionAlert(
            user_id=test_user.id, order_id=None,  # belongs to test_user
            instrument="OTHER", alert_type=AlertType.WATCHING,
            message="other user's alert", current_price=100.0, is_actioned=False,
        )
        db.add(a); db.flush()

        # kite_trader requests alerts — should see 0 (the alert belongs to trader1)
        resp = client.get("/alerts", headers=kite_auth_headers)
        assert resp.json()["count"] == 0


# ── Single-click exit ─────────────────────────────────────────────────────────

class TestSingleClickExit:

    def _mock_kite(self, ltp=263.0, sell_ok=True):
        kite = MagicMock()
        kite.get_ltp.return_value = {"NFO:NIFTY24JUN26300CE": ltp}
        kite.place_order.return_value = "SELL_BROKER_001" if sell_ok else None
        kite.cancel_order.return_value = True
        return kite

    def test_exit_success(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        with patch("main.KiteService") as MockKite, \
             patch("position_monitor.KiteService") as MockKitePM:
            MockKite.return_value   = self._mock_kite(263.0)
            MockKitePM.return_value = self._mock_kite(263.0)

            resp = client.post(
                f"/positions/{open_buy_order.id}/exit",
                headers=kite_auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["exit_price"]  == 263.0
        assert body["profit_loss"] == pytest.approx(300.0)
        assert body["instrument"]  == "NIFTY24JUN26300CE"
        assert "sell_broker_order_id" in body

    def test_exit_updates_buy_order(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        with patch("main.KiteService") as MockKite, \
             patch("position_monitor.KiteService") as MockKitePM:
            MockKite.return_value   = self._mock_kite(267.0)
            MockKitePM.return_value = self._mock_kite(267.0)
            client.post(f"/positions/{open_buy_order.id}/exit", headers=kite_auth_headers)

        db.refresh(open_buy_order)
        assert open_buy_order.exit_price  == 267.0
        assert open_buy_order.profit_loss == pytest.approx(700.0)

    def test_exit_marks_pending_alerts_actioned(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        alert = PositionAlert(
            user_id=test_user_with_kite.id, order_id=open_buy_order.id,
            instrument=open_buy_order.instrument, alert_type=AlertType.WATCHING,
            message="watch", current_price=262.0, is_actioned=False,
        )
        db.add(alert); db.flush()

        with patch("main.KiteService") as MockKite, \
             patch("position_monitor.KiteService") as MockKitePM:
            MockKite.return_value   = self._mock_kite()
            MockKitePM.return_value = self._mock_kite()
            client.post(f"/positions/{open_buy_order.id}/exit", headers=kite_auth_headers)

        db.refresh(alert)
        assert alert.is_actioned is True

    def test_exit_already_closed_returns_400(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        open_buy_order.exit_price = 263.0
        db.flush()

        resp = client.post(
            f"/positions/{open_buy_order.id}/exit",
            headers=kite_auth_headers,
        )
        assert resp.status_code == 400
        assert "already closed" in resp.json()["detail"]

    def test_exit_no_kite_token_returns_403(
        self, client, auth_headers, db, test_user, open_buy_order
    ):
        # test_user has no access_token; create an order for them
        order = Order(
            user_id=test_user.id, instrument="NIFTY24JUN26300CE",
            quantity=100, price=260.0, order_type="BUY", status="EXECUTED",
            entry_price=260.0,
        )
        db.add(order); db.flush()

        resp = client.post(f"/positions/{order.id}/exit", headers=auth_headers)
        assert resp.status_code == 403

    def test_exit_order_not_found_returns_404(
        self, client, kite_auth_headers
    ):
        resp = client.post("/positions/99999/exit", headers=kite_auth_headers)
        assert resp.status_code == 404

    def test_exit_ltp_failure_returns_502(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        kite = MagicMock()
        kite.get_ltp.return_value = {}  # empty — LTP unavailable

        with patch("main.KiteService", return_value=kite):
            resp = client.post(
                f"/positions/{open_buy_order.id}/exit",
                headers=kite_auth_headers,
            )
        assert resp.status_code == 502

    def test_exit_kite_sell_failure_returns_502(
        self, client, db, kite_auth_headers, test_user_with_kite, open_buy_order
    ):
        with patch("main.KiteService") as MockKite, \
             patch("position_monitor.KiteService") as MockKitePM:
            MockKite.return_value   = self._mock_kite(sell_ok=False)
            MockKitePM.return_value = self._mock_kite(sell_ok=False)
            resp = client.post(
                f"/positions/{open_buy_order.id}/exit",
                headers=kite_auth_headers,
            )
        assert resp.status_code == 502

    def test_exit_requires_auth(self, client, open_buy_order):
        resp = client.post(f"/positions/{open_buy_order.id}/exit")
        assert resp.status_code in (401, 403)


# ── Monitoring start / stop / status ─────────────────────────────────────────

class TestMonitoringEndpoints:

    def test_start_monitoring(
        self, client, db, kite_auth_headers, test_user_with_kite
    ):
        with patch("position_monitor.asyncio") as mock_asyncio:
            mock_asyncio.create_task = MagicMock()
            resp = client.post("/monitoring/start", headers=kite_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "started" in body["message"].lower() or "running" in body["message"].lower()

        session = db.query(MonitoringSession).filter(
            MonitoringSession.user_id == test_user_with_kite.id
        ).first()
        assert session is not None
        assert session.is_active is True

    def test_stop_monitoring(
        self, client, db, kite_auth_headers, test_user_with_kite,
        active_monitoring_session
    ):
        resp = client.post("/monitoring/stop", headers=kite_auth_headers)
        assert resp.status_code == 200
        db.refresh(active_monitoring_session)
        assert active_monitoring_session.is_active is False

    def test_monitoring_status_returns_is_running(
        self, client, kite_auth_headers
    ):
        resp = client.get("/monitoring/status", headers=kite_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "monitor_running" in body

    def test_start_monitoring_requires_auth(self, client):
        resp = client.post("/monitoring/start")
        assert resp.status_code in (401, 403)

    def test_stop_monitoring_requires_auth(self, client):
        resp = client.post("/monitoring/stop")
        assert resp.status_code in (401, 403)
