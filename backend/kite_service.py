"""
Zerodha KiteConnect service wrapper for real order placement and market data.
"""

import os
import logging
from typing import List, Dict, Optional
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


class KiteService:
    """Wraps KiteConnect SDK for order management and market data."""

    def __init__(self, access_token: str):
        self.kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
        self.kite.set_access_token(access_token)

    # ------------------------------------------------------------------
    # Auth helpers (used before access_token is available)
    # ------------------------------------------------------------------

    @staticmethod
    def get_login_url() -> str:
        """Return Kite login URL for OAuth flow."""
        kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
        return kite.login_url()

    @staticmethod
    def generate_session(request_token: str) -> Dict:
        """
        Exchange request_token for access_token.
        Returns the full session dict (access_token, user info, etc.)
        """
        kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
        session = kite.generate_session(
            request_token=request_token,
            api_secret=os.getenv("KITE_API_SECRET"),
        )
        return session

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_ltp(self, instruments: List[str]) -> Dict[str, float]:
        """
        Get Last Traded Price for a list of instruments.
        instruments format: ["NFO:NIFTY24DEC24000CE", ...]
        Returns: {"NFO:NIFTY24DEC24000CE": 152.5, ...}
        """
        try:
            quote = self.kite.ltp(instruments)
            return {symbol: data["last_price"] for symbol, data in quote.items()}
        except Exception as e:
            logger.error(f"LTP fetch failed: {e}")
            return {}

    def get_historical_data(
        self,
        instrument_token: int,
        from_date: str,
        to_date: str,
        interval: str = "60minute",
    ) -> List[Dict]:
        """
        Fetch OHLCV historical data.
        interval options: minute, 3minute, 5minute, 15minute, 30minute, 60minute, day
        Returns list of dicts with keys: date, open, high, low, close, volume
        """
        try:
            records = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
            )
            return records
        except Exception as e:
            logger.error(f"Historical data fetch failed: {e}")
            return []

    def get_option_instruments(self, underlying: str = "NIFTY") -> List[Dict]:
        """
        Fetch NFO instruments filtered to call options for a given underlying.
        Returns list of instrument dicts.
        """
        try:
            instruments = self.kite.instruments("NFO")
            return [
                inst for inst in instruments
                if inst["name"] == underlying and inst["instrument_type"] == "CE"
            ]
        except Exception as e:
            logger.error(f"Instruments fetch failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def place_order(
        self,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        product: str = "MIS",
    ) -> Optional[str]:
        """
        Place a real order on Zerodha.

        Args:
            tradingsymbol: e.g. "NIFTY24DEC24000CE"
            transaction_type: "BUY" or "SELL"
            quantity: number of lots (each NIFTY lot = 25 units)
            order_type: "MARKET" or "LIMIT"
            price: required if order_type is "LIMIT"
            product: "MIS" (intraday) or "NRML" (carry forward)

        Returns:
            broker order_id string, or None on failure
        """
        try:
            kite_txn = (
                self.kite.TRANSACTION_TYPE_BUY
                if transaction_type == "BUY"
                else self.kite.TRANSACTION_TYPE_SELL
            )
            kite_order_type = (
                self.kite.ORDER_TYPE_MARKET
                if order_type == "MARKET"
                else self.kite.ORDER_TYPE_LIMIT
            )
            kite_product = (
                self.kite.PRODUCT_MIS if product == "MIS" else self.kite.PRODUCT_NRML
            )

            params = dict(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=tradingsymbol,
                transaction_type=kite_txn,
                quantity=quantity,
                product=kite_product,
                order_type=kite_order_type,
            )
            if order_type == "LIMIT" and price is not None:
                params["price"] = price

            order_id = self.kite.place_order(**params)
            logger.info(
                f"Order placed on Kite: {transaction_type} {quantity} {tradingsymbol} "
                f"[{order_type}] → order_id={order_id}"
            )
            return str(order_id)

        except Exception as e:
            logger.error(f"Kite order placement failed: {e}")
            return None

    def modify_order(
        self,
        order_id: str,
        price: Optional[float] = None,
        quantity: Optional[int] = None,
        order_type: Optional[str] = None,
        trigger_price: Optional[float] = None,
    ) -> bool:
        """
        Modify a pending order on Zerodha.
        Only PENDING orders can be modified; EXECUTED orders cannot.
        """
        try:
            params: Dict = dict(
                variety=self.kite.VARIETY_REGULAR,
                order_id=order_id,
            )
            if price is not None:
                params["price"] = price
            if quantity is not None:
                params["quantity"] = quantity
            if order_type is not None:
                params["order_type"] = (
                    self.kite.ORDER_TYPE_MARKET if order_type == "MARKET"
                    else self.kite.ORDER_TYPE_LIMIT
                )
            if trigger_price is not None:
                params["trigger_price"] = trigger_price

            self.kite.modify_order(**params)
            logger.info(f"Order modified: {order_id} → {params}")
            return True
        except Exception as e:
            logger.error(f"Order modification failed for {order_id}: {e}")
            return False

    def place_sl_order(
        self,
        tradingsymbol: str,
        quantity: int,
        trigger_price: float,
        product: str = "MIS",
    ) -> Optional[str]:
        """
        Place a Stop-Loss Market (SL-M) SELL order.
        Fires a market sell when price drops to trigger_price.
        Used to protect a BUY position with a hard stop-loss.
        """
        try:
            kite_product = self.kite.PRODUCT_MIS if product == "MIS" else self.kite.PRODUCT_NRML
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=tradingsymbol,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                product=kite_product,
                order_type=self.kite.ORDER_TYPE_SLM,
                trigger_price=round(trigger_price, 1),
            )
            logger.info(f"SL-M order placed: SELL {quantity} {tradingsymbol} trigger=₹{trigger_price:.1f} → order_id={order_id}")
            return str(order_id)
        except Exception as e:
            logger.error(f"SL order placement failed: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order by broker order_id."""
        try:
            self.kite.cancel_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=order_id,
            )
            logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Order cancellation failed for {order_id}: {e}")
            return False

    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """Fetch status of a single order from Kite."""
        try:
            orders = self.kite.orders()
            for order in orders:
                if str(order["order_id"]) == str(order_id):
                    return order
            return None
        except Exception as e:
            logger.error(f"Order status fetch failed: {e}")
            return None

    def get_positions(self) -> Dict:
        """Fetch current open positions."""
        try:
            return self.kite.positions()
        except Exception as e:
            logger.error(f"Positions fetch failed: {e}")
            return {"net": [], "day": []}

    def get_margins(self) -> Dict:
        """Fetch available margins."""
        try:
            return self.kite.margins()
        except Exception as e:
            logger.error(f"Margins fetch failed: {e}")
            return {}
