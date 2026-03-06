"""
Automated trading engine with RSI and MACD indicators
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import numpy as np
from sqlalchemy.orm import Session
from database import SessionLocal
from models import TradingStrategy, Order, MarketData, User
import json
import os

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

class TradingEngine:
    def __init__(self):
        self.db = SessionLocal()
        self.max_investment = 10000.0
        self.profit_target_min = 300.0
        self.profit_target_max = 500.0
        
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
    
    def get_historical_prices(self, instrument: str, days: int = 30) -> List[float]:
        """Get historical prices for technical analysis"""
        # In a real implementation, this would fetch from Kite API
        # For now, return sample data
        
        # Generate realistic price movement
        base_price = 150.0
        prices = []
        current_price = base_price
        
        for i in range(days * 24):  # Hourly data for better resolution
            # Random walk with some trend
            change = np.random.normal(0, 0.02) * current_price
            current_price = max(current_price + change, base_price * 0.5)
            prices.append(current_price)
        
        return prices
    
    def should_buy(self, strategy: TradingStrategy, current_price: float) -> bool:
        """Determine if conditions are met for buying"""
        try:
            # Get historical data
            prices = self.get_historical_prices(strategy.instrument)
            
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
            
            # Calculate current P&L
            quantity = order.quantity
            investment = order.entry_price * quantity
            current_value = current_price * quantity
            current_pnl = current_value - investment
            
            logger.info(f"P&L Analysis: Entry={order.entry_price}, Current={current_price}, P&L=₹{current_pnl:.2f}")
            
            # Check profit targets
            if current_pnl >= self.profit_target_min and current_pnl <= self.profit_target_max:
                logger.info(f"Profit target met: ₹{current_pnl:.2f}")
                return True
            
            # Check stop loss (20% loss)
            stop_loss_threshold = investment * 0.2
            if current_pnl <= -stop_loss_threshold:
                logger.warning(f"Stop loss triggered: ₹{current_pnl:.2f}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error in sell analysis: {str(e)}")
            return False
    
    def get_current_price(self, instrument: str) -> float:
        """Get current market price (mock implementation)"""
        # In real implementation, this would call Kite API
        # For now, return a random price around base value
        base_prices = {
            "NIFTY24DEC24000CE": 150.0,
            "BANKNIFTY24DEC51000CE": 200.0,
            "RELIANCE24DEC3000CE": 25.0
        }
        
        base_price = base_prices.get(instrument, 100.0)
        # Add some random movement
        variation = np.random.normal(0, 0.05) * base_price
        return max(base_price + variation, base_price * 0.1)
    
    def place_buy_order(self, strategy: TradingStrategy, price: float) -> Optional[Order]:
        """Place a buy order"""
        try:
            quantity = int(self.max_investment / price)
            if quantity == 0:
                return None
            
            order = Order(
                user_id=strategy.user_id,
                instrument=strategy.instrument,
                quantity=quantity,
                price=price,
                order_type='BUY',
                status='EXECUTED',  # Assume immediate execution for demo
                strategy_id=strategy.id,
                entry_price=price,
                executed_at=datetime.now()
            )
            
            self.db.add(order)
            self.db.commit()
            
            logger.info(f"Buy order placed: {quantity} x {strategy.instrument} @ ₹{price}")
            return order
            
        except Exception as e:
            logger.error(f"Error placing buy order: {str(e)}")
            self.db.rollback()
            return None
    
    def place_sell_order(self, buy_order: Order, price: float) -> Optional[Order]:
        """Place a sell order"""
        try:
            sell_order = Order(
                user_id=buy_order.user_id,
                instrument=buy_order.instrument,
                quantity=buy_order.quantity,
                price=price,
                order_type='SELL',
                status='EXECUTED',  # Assume immediate execution for demo
                strategy_id=buy_order.strategy_id,
                exit_price=price,
                executed_at=datetime.now()
            )
            
            # Calculate P&L
            investment = buy_order.entry_price * buy_order.quantity
            proceeds = price * buy_order.quantity
            profit_loss = proceeds - investment
            sell_order.profit_loss = profit_loss
            
            # Update buy order
            buy_order.exit_price = price
            buy_order.profit_loss = profit_loss
            
            self.db.add(sell_order)
            self.db.commit()
            
            logger.info(f"Sell order placed: {buy_order.quantity} x {buy_order.instrument} @ ₹{price}, P&L: ₹{profit_loss:.2f}")
            return sell_order
            
        except Exception as e:
            logger.error(f"Error placing sell order: {str(e)}")
            self.db.rollback()
            return None
    
    def process_strategy(self, strategy: TradingStrategy):
        """Process a single trading strategy"""
        try:
            logger.info(f"Processing strategy: {strategy.name} ({strategy.instrument})")
            
            # Get current price
            current_price = self.get_current_price(strategy.instrument)
            logger.info(f"Current price for {strategy.instrument}: ₹{current_price}")
            
            # Check for open positions
            open_orders = self.db.query(Order).filter(
                Order.strategy_id == strategy.id,
                Order.order_type == 'BUY',
                Order.status == 'EXECUTED',
                Order.exit_price.is_(None)
            ).all()
            
            if open_orders:
                # Check if we should sell any positions
                for order in open_orders:
                    if self.should_sell(order, current_price):
                        self.place_sell_order(order, current_price)
            else:
                # Check if we should buy
                if self.should_buy(strategy, current_price):
                    self.place_buy_order(strategy, current_price)
                    
        except Exception as e:
            logger.error(f"Error processing strategy {strategy.id}: {str(e)}")
    
    def run_trading_cycle(self):
        """Run one trading cycle for all active automated strategies"""
        try:
            logger.info("Starting trading cycle...")
            
            # Get all active automated strategies
            strategies = self.db.query(TradingStrategy).filter(
                TradingStrategy.is_active == True,
                TradingStrategy.strategy_type == 'AUTOMATED'
            ).all()
            
            logger.info(f"Found {len(strategies)} active automated strategies")
            
            for strategy in strategies:
                self.process_strategy(strategy)
            
            logger.info("Trading cycle completed")
            
        except Exception as e:
            logger.error(f"Error in trading cycle: {str(e)}")
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