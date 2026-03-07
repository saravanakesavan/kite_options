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