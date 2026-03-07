"""
Automated trading engine with RSI and MACD indicators backed by Zerodha Kite API.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import numpy as np
from sqlalchemy.orm import Session

from database import SessionLocal
from kite_service import KiteService
from models import TradingStrategy, Order, MarketData, User

# Try to import pandas and ta, handle missing dependencies
try:
    import pandas as pd
    import ta
    TECHNICAL_ANALYSIS_AVAILABLE = True
except ImportError:
    TECHNICAL_ANALYSIS_AVAILABLE = False
    logging.warning("pandas or ta not available. Technical analysis features will be limited.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Trading hours gate: 9:30 AM – 1:30 PM IST
TRADE_START = (9, 30)
TRADE_END = (13, 30)


def _within_trading_hours() -> bool:
    now = datetime.now()
    start = now.replace(hour=TRADE_START[0], minute=TRADE_START[1], second=0, microsecond=0)
    end = now.replace(hour=TRADE_END[0], minute=TRADE_END[1], second=0, microsecond=0)
    return start <= now <= end

class TradingEngine:
    def __init__(self, access_token: str):
        self.db = SessionLocal()
        self.kite = KiteService(access_token)
        self.max_investment = float(os.getenv("MAX_INVESTMENT_PER_TRADE", 10000))
        self.profit_target_min = float(os.getenv("PROFIT_TARGET_MIN", 300))
        self.profit_target_max = float(os.getenv("PROFIT_TARGET_MAX", 500))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PERCENTAGE", 20)) / 100
        
    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calculate RSI indicator"""
        if not TECHNICAL_ANALYSIS_AVAILABLE or len(prices) < period:
            return 50.0  # Neutral RSI if not enough data or libraries unavailable
        
        try:
            df = pd.DataFrame({'close': prices})
            rsi = ta.momentum.RSIIndicator(df['close'], window=period)
            return rsi.rsi().iloc[-1]
        except Exception as e:
            logger.warning(f"RSI calculation failed: {e}")
            return 50.0
    
    def calculate_macd(self, prices: List[float]) -> Dict[str, float]:
        """Calculate MACD indicator"""
        if not TECHNICAL_ANALYSIS_AVAILABLE or len(prices) < 26:
            return {'macd': 0.0, 'signal': 0.0, 'histogram': 0.0}
        
        try:
            df = pd.DataFrame({'close': prices})
            macd = ta.trend.MACD(df['close'])
            
            return {
                'macd': macd.macd().iloc[-1] if not pd.isna(macd.macd().iloc[-1]) else 0.0,
                'signal': macd.macd_signal().iloc[-1] if not pd.isna(macd.macd_signal().iloc[-1]) else 0.0,
                'histogram': macd.macd_diff().iloc[-1] if not pd.isna(macd.macd_diff().iloc[-1]) else 0.0
            }
        except Exception as e:
            logger.warning(f"MACD calculation failed: {e}")
            return {'macd': 0.0, 'signal': 0.0, 'histogram': 0.0}
    
    def get_historical_prices(self, instrument_token: int, days: int = 30) -> List[float]:
        """Fetch real hourly close prices from Kite for technical analysis."""
        to_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        records = self.kite.get_historical_data(
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval="60minute",
        )
        if not records:
            logger.warning(f"No historical data for token {instrument_token}, using empty list")
            return []
        return [r["close"] for r in records]
    
    def should_buy(self, strategy: TradingStrategy, current_price: float, instrument_token: int) -> bool:
        """Determine if conditions are met for buying"""
        try:
            prices = self.get_historical_prices(instrument_token)
            
            # Calculate indicators
            rsi = self.calculate_rsi(prices) if strategy.use_rsi else 50
            macd_data = self.calculate_macd(prices) if strategy.use_macd else {'histogram': 0}
            
            logger.info(f"Analysis for {strategy.instrument}: RSI={rsi:.2f}, MACD Histogram={macd_data['histogram']:.4f}")
            
            # Check investment limit
            max_quantity = int(self.max_investment / current_price)
            if max_quantity == 0:
                logger.warning(f"Price {current_price} too high for ₹{self.max_investment} budget")
                return False
            
            # Technical analysis conditions
            buy_signals = []
            
            if strategy.use_rsi:
                rsi_signal = rsi <= strategy.rsi_oversold
                buy_signals.append(rsi_signal)
                logger.info(f"RSI Signal: {rsi_signal} (RSI: {rsi}, Threshold: {strategy.rsi_oversold})")
            
            if strategy.use_macd:
                macd_signal = macd_data['histogram'] > strategy.macd_signal_threshold
                buy_signals.append(macd_signal)
                logger.info(f"MACD Signal: {macd_signal} (Histogram: {macd_data['histogram']}, Threshold: {strategy.macd_signal_threshold})")
            
            # All enabled signals must be positive
            return all(buy_signals) if buy_signals else False
            
        except Exception as e:
            logger.error(f"Error in buy analysis: {str(e)}")
            return False
    
    def should_sell(self, order: Order, current_price: float) -> bool:
        """Determine if conditions are met for selling"""
        try:
            if order.entry_price is None:
                return False

            quantity = order.quantity
            investment = order.entry_price * quantity
            current_value = current_price * quantity
            current_pnl = current_value - investment

            logger.info(f"P&L Analysis: Entry={order.entry_price}, Current={current_price}, P&L=₹{current_pnl:.2f}")

            if self.profit_target_min <= current_pnl <= self.profit_target_max:
                logger.info(f"Profit target met: ₹{current_pnl:.2f}")
                return True

            if current_pnl <= -(investment * self.stop_loss_pct):
                logger.warning(f"Stop loss triggered: ₹{current_pnl:.2f}")
                return True

            return False

        except Exception as e:
            logger.error(f"Error in sell analysis: {str(e)}")
            return False
    
    def get_current_price(self, instrument: str) -> float:
        """Get real Last Traded Price from Kite for an NFO instrument."""
        key = f"NFO:{instrument}"
        prices = self.kite.get_ltp([key])
        price = prices.get(key)
        if price is None:
            logger.warning(f"LTP unavailable for {instrument}")
            return 0.0
        return price
    
    def place_buy_order(self, strategy: TradingStrategy, price: float) -> Optional[Order]:
        """Place a real BUY order via Kite and record it locally."""
        try:
            quantity = int(self.max_investment / price)
            if quantity == 0:
                logger.warning(f"Price ₹{price} too high for ₹{self.max_investment} budget")
                return None

            broker_order_id = self.kite.place_order(
                tradingsymbol=strategy.instrument,
                transaction_type="BUY",
                quantity=quantity,
                order_type="MARKET",
            )
            if broker_order_id is None:
                return None

            order = Order(
                user_id=strategy.user_id,
                instrument=strategy.instrument,
                quantity=quantity,
                price=price,
                order_type="BUY",
                status="EXECUTED",
                broker_order_id=broker_order_id,
                strategy_id=strategy.id,
                entry_price=price,
                executed_at=datetime.now(),
            )

            self.db.add(order)
            self.db.commit()
            logger.info(f"Buy order placed: {quantity} x {strategy.instrument} @ ₹{price}, broker_id={broker_order_id}")
            return order

        except Exception as e:
            logger.error(f"Error placing buy order: {e}")
            self.db.rollback()
            return None

    def place_sell_order(self, buy_order: Order, price: float) -> Optional[Order]:
        """Place a real SELL order via Kite and record P&L locally."""
        try:
            broker_order_id = self.kite.place_order(
                tradingsymbol=buy_order.instrument,
                transaction_type="SELL",
                quantity=buy_order.quantity,
                order_type="MARKET",
            )
            if broker_order_id is None:
                return None

            investment = buy_order.entry_price * buy_order.quantity
            proceeds = price * buy_order.quantity
            profit_loss = proceeds - investment

            sell_order = Order(
                user_id=buy_order.user_id,
                instrument=buy_order.instrument,
                quantity=buy_order.quantity,
                price=price,
                order_type="SELL",
                status="EXECUTED",
                broker_order_id=broker_order_id,
                strategy_id=buy_order.strategy_id,
                exit_price=price,
                profit_loss=profit_loss,
                executed_at=datetime.now(),
            )

            buy_order.exit_price = price
            buy_order.profit_loss = profit_loss

            self.db.add(sell_order)
            self.db.commit()
            logger.info(f"Sell order placed: {buy_order.quantity} x {buy_order.instrument} @ ₹{price}, P&L: ₹{profit_loss:.2f}, broker_id={broker_order_id}")
            return sell_order

        except Exception as e:
            logger.error(f"Error placing sell order: {e}")
            self.db.rollback()
            return None
    
    def _resolve_instrument_token(self, tradingsymbol: str) -> Optional[int]:
        """Look up the NFO instrument token for a given trading symbol."""
        try:
            instruments = self.kite.kite.instruments("NFO")
            for inst in instruments:
                if inst["tradingsymbol"] == tradingsymbol:
                    return inst["instrument_token"]
            logger.warning(f"Instrument token not found for {tradingsymbol}")
            return None
        except Exception as e:
            logger.error(f"Instrument token lookup failed: {e}")
            return None

    def process_strategy(self, strategy: TradingStrategy):
        """Process a single trading strategy using real Kite data."""
        try:
            logger.info(f"Processing strategy: {strategy.name} ({strategy.instrument})")

            current_price = self.get_current_price(strategy.instrument)
            if current_price == 0.0:
                logger.warning(f"Skipping {strategy.instrument} — LTP unavailable")
                return

            logger.info(f"Current price for {strategy.instrument}: ₹{current_price}")

            open_orders = self.db.query(Order).filter(
                Order.strategy_id == strategy.id,
                Order.order_type == "BUY",
                Order.status == "EXECUTED",
                Order.exit_price.is_(None),
            ).all()

            if open_orders:
                for order in open_orders:
                    if self.should_sell(order, current_price):
                        self.place_sell_order(order, current_price)
            else:
                instrument_token = self._resolve_instrument_token(strategy.instrument)
                if instrument_token and self.should_buy(strategy, current_price, instrument_token):
                    self.place_buy_order(strategy, current_price)

        except Exception as e:
            logger.error(f"Error processing strategy {strategy.id}: {e}")
    
    def run_trading_cycle(self):
        """Run one trading cycle for all active automated strategies."""
        if not _within_trading_hours():
            logger.info("Outside trading hours (9:30–13:30). Skipping cycle.")
            return

        try:
            logger.info("Starting trading cycle...")

            strategies = self.db.query(TradingStrategy).filter(
                TradingStrategy.is_active == True,
                TradingStrategy.strategy_type == "AUTOMATED",
            ).all()

            logger.info(f"Found {len(strategies)} active automated strategies")

            for strategy in strategies:
                self.process_strategy(strategy)

            logger.info("Trading cycle completed")

        except Exception as e:
            logger.error(f"Error in trading cycle: {e}")
        finally:
            self.db.close()

# Scheduler function to run every minute
async def run_automated_trading():
    """Main function to run automated trading"""
    while True:
        try:
            engine = TradingEngine()
            engine.run_trading_cycle()
            
            # Wait for 1 minute
            await asyncio.sleep(60)
            
        except Exception as e:
            logger.error(f"Error in automated trading loop: {str(e)}")
            await asyncio.sleep(60)  # Still wait even if there's an error

if __name__ == "__main__":
    # Run the trading engine
    asyncio.run(run_automated_trading())