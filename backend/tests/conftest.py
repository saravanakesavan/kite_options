"""
Shared pytest fixtures for all test modules.

Uses an in-memory SQLite DB per test session and overrides FastAPI's get_db
dependency so no real database is touched.  KiteService is always mocked.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import get_db
from models import Base, User, Order, MonitoringSession, PositionAlert
from auth import AuthService
from main import app

# ── In-memory test database ───────────────────────────────────────────────────

TEST_DB_URL = "sqlite:///:memory:"

@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture(scope="function")
def db(engine):
    """Fresh transaction per test — rolled back at the end."""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(db):
    """TestClient with get_db overridden to use the test session."""
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ── Common data fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def test_user(db):
    user = User(
        username        = "trader1",
        email           = "trader1@test.com",
        hashed_password = AuthService.get_password_hash("secret123"),
        is_active       = True,
        access_token    = None,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def test_user_with_kite(db):
    user = User(
        username        = "kite_trader",
        email           = "kite@test.com",
        hashed_password = AuthService.get_password_hash("secret123"),
        is_active       = True,
        access_token    = "fake_kite_token",
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def auth_headers(client, test_user, db):
    """Log in and return Bearer auth headers."""
    resp = client.post("/auth/login", json={
        "username": "trader1",
        "password": "secret123",
    })
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def kite_auth_headers(client, test_user_with_kite, db):
    """Auth headers for a user with a linked Kite account."""
    resp = client.post("/auth/login", json={
        "username": "kite_trader",
        "password": "secret123",
    })
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def open_buy_order(db, test_user_with_kite):
    """An executed BUY order with no exit yet — ready to be monitored."""
    order = Order(
        user_id     = test_user_with_kite.id,
        instrument  = "NIFTY24JUN26300CE",
        quantity    = 100,
        price       = 260.0,
        order_type  = "BUY",
        status      = "EXECUTED",
        entry_price = 260.0,
        sl_percentage       = 3.0,
        sl_broker_order_id  = "SL_BROKER_001",
        broker_order_id     = "BUY_BROKER_001",
    )
    db.add(order)
    db.flush()
    return order


@pytest.fixture
def active_monitoring_session(db, test_user_with_kite):
    ms = MonitoringSession(user_id=test_user_with_kite.id, is_active=True)
    db.add(ms)
    db.flush()
    return ms
