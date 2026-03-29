"""
Database models for the options trading app
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Kite API credentials (encrypted)
    api_key = Column(String(255), nullable=True)
    access_token = Column(String(255), nullable=True)
    
    # Relationships
    orders = relationship("Order", back_populates="user")
    strategies = relationship("TradingStrategy", back_populates="user")

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Order details
    instrument = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    order_type = Column(String(10), nullable=False)  # 'BUY', 'SELL'
    status = Column(String(20), default='PENDING')  # 'PENDING', 'EXECUTED', 'CANCELLED', 'REJECTED'
    
    # External order ID from broker
    broker_order_id = Column(String(100), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    executed_at = Column(DateTime(timezone=True), nullable=True)
    
    # P&L tracking
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    profit_loss = Column(Float, default=0.0)

    # Stop-loss tracking
    sl_percentage = Column(Float, nullable=True)          # e.g. 3.0 for 3%
    sl_trigger_price = Column(Float, nullable=True)       # computed trigger price
    sl_broker_order_id = Column(String(100), nullable=True)  # Kite SL order id

    # Associated strategy
    strategy_id = Column(Integer, ForeignKey("trading_strategies.id"), nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="orders")
    strategy = relationship("TradingStrategy", back_populates="orders")

class TradingStrategy(Base):
    __tablename__ = "trading_strategies"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Strategy configuration
    name = Column(String(100), nullable=False)
    strategy_type = Column(String(20), nullable=False)  # 'MANUAL', 'AUTOMATED'
    instrument = Column(String(100), nullable=False)
    
    # Risk management
    max_investment = Column(Float, default=10000.0)
    profit_target_min = Column(Float, default=300.0)
    profit_target_max = Column(Float, default=500.0)
    stop_loss_percentage = Column(Float, default=20.0)  # 20% stop loss
    
    # Technical indicators for automated strategy
    use_rsi = Column(Boolean, default=True)
    rsi_oversold = Column(Integer, default=30)
    rsi_overbought = Column(Integer, default=70)
    
    use_macd = Column(Boolean, default=True)
    macd_signal_threshold = Column(Float, default=0.0)
    
    # Strategy status
    is_active = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="strategies")
    orders = relationship("Order", back_populates="strategy")

class MarketData(Base):
    __tablename__ = "market_data"

    id = Column(Integer, primary_key=True, index=True)
    instrument = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # OHLC data
    open_price = Column(Float, nullable=False)
    high_price = Column(Float, nullable=False)
    low_price = Column(Float, nullable=False)
    close_price = Column(Float, nullable=False)
    volume = Column(Integer, default=0)
    
    # Technical indicators
    rsi = Column(Float, nullable=True)
    macd = Column(Float, nullable=True)
    macd_signal = Column(Float, nullable=True)
    macd_histogram = Column(Float, nullable=True)

class SignalRecord(Base):
    """Persisted signal evaluation — used for queue-based prediction."""
    __tablename__ = "signal_records"

    id = Column(Integer, primary_key=True, index=True)
    instrument = Column(String(100), nullable=False, index=True)
    evaluated_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Raw indicator values at evaluation time
    rsi = Column(Float, nullable=True)
    rsi_prev = Column(Float, nullable=True)          # one candle ago
    macd_histogram = Column(Float, nullable=True)
    macd_histogram_prev = Column(Float, nullable=True)
    macd_line = Column(Float, nullable=True)
    signal_line = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    price_trend_pct = Column(Float, nullable=True)   # (close[-1] - close[-6]) / close[-6] * 100

    # Derived signal
    direction = Column(String(10), nullable=False)   # "BUY", "SELL", "HOLD"
    confidence = Column(Float, default=0.0)          # 0–100
    reasons = Column(Text, nullable=True)            # JSON list of reason strings

    # Link to strategy that triggered the scan (optional)
    strategy_id = Column(Integer, ForeignKey("trading_strategies.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)


class AlertType:
    """String constants for PositionAlert.alert_type — avoids scattered raw strings."""
    PROFIT_TARGET   = "PROFIT_TARGET"
    EXTENDED_PROFIT = "EXTENDED_PROFIT"
    STOP_LOSS       = "STOP_LOSS"
    FORCE_EXIT      = "FORCE_EXIT"
    WATCHING        = "WATCHING"


class MonitoringSession(Base):
    """
    Persists per-user monitoring state so it survives server restarts.
    Source of truth for whether a user's positions should be monitored.
    """
    __tablename__ = "monitoring_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    last_heartbeat = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")


class PositionAlert(Base):
    """
    Actionable alert written by the monitor each time a position needs attention.
    The frontend polls GET /alerts to show these to the user.
    """
    __tablename__ = "position_alerts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)

    instrument = Column(String(100), nullable=False)
    alert_type = Column(String(30), nullable=False)
    # PROFIT_TARGET | EXTENDED_PROFIT | STOP_LOSS | FORCE_EXIT | WATCHING

    current_price = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    message = Column(String(500), nullable=False)

    is_actioned = Column(Boolean, default=False)   # True once user clicks exit or dismisses
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    user = relationship("User")
    order = relationship("Order")


class TradingSession(Base):
    __tablename__ = "trading_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Session details
    session_date = Column(DateTime(timezone=True), server_default=func.now())
    total_trades = Column(Integer, default=0)
    successful_trades = Column(Integer, default=0)
    total_pnl = Column(Float, default=0.0)

    # Session status
    is_active = Column(Boolean, default=True)

    # Relationships
    user = relationship("User")


class MockTrade(Base):
    """
    Paper / mock trade record.
    Stores the entry price at 'buy' time and lets the frontend
    compute live P&L by comparing against Kite LTP.
    Max 5 open positions per user enforced at the API layer.
    """
    __tablename__ = "mock_trades"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Instrument details
    instrument = Column(String(100), nullable=False)   # e.g. "NIFTY25JUN24500CE"
    quantity = Column(Integer, nullable=False, default=1)
    entry_price = Column(Float, nullable=False)        # LTP at time of mock-buy

    # Optional label / notes
    notes = Column(String(255), nullable=True)

    # Win Probability engine score at time of mock-buy (for performance analytics)
    # NULL if trade was opened manually (not from Win Probability page)
    win_probability_score = Column(Float, nullable=True)   # 0–100
    win_probability_grade = Column(String(4), nullable=True)  # A+/A/B/C/D

    # Timestamps
    entry_time = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Status: OPEN | CLOSED
    status = Column(String(10), default="OPEN", nullable=False)

    # Exit tracking (filled when user closes position)
    exit_price = Column(Float, nullable=True)
    exit_time = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")