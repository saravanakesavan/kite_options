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

    def validate_session(self) -> None:
        """
        Validate the access token against a market-data endpoint.
        Raises KiteSessionExpiredError if the token has expired or is invalid.
        Raises KitePermissionError if the API key lacks market-data permissions.

        NOTE: profile() does NOT reliably catch expired tokens — some Kite plans
        let profile() succeed while ltp/quote/historical fail with PermissionException.
        We use ltp(NIFTY 50) as the canary since it's the same scope as ranking endpoints.
        """
        try:
            result = self.kite.ltp(["NSE:NIFTY 50"])
            if not result:
                raise KiteSessionExpiredError(
                    "Kite session check returned empty response — please re-link."
                )
        except (KiteSessionExpiredError, KitePermissionError):
            raise  # already the right type
        except Exception as e:
            err_str = str(e).lower()
            if "permission" in err_str:
                # PermissionException on market data = Personal plan limitation.
                # The free Kite Connect Personal plan has NO market data access at all
                # (no ltp, no quote, no historical data). Re-linking will NOT fix this.
                # Fix: create a paid Kite Connect app at developers.kite.trade (₹500/month).
                # Historical data is included free with the paid plan (no extra add-on needed).
                raise KitePermissionError(
                    "KITE_NO_MARKET_DATA: Your API key is on the Kite Connect Personal plan "
                    "which does not include market data (ltp/quote/historical). "
                    "Create a paid Kite Connect app at developers.kite.trade (₹500/month). "
                    "Historical data is included free with the paid plan."
                ) from e
            if any(kw in err_str for kw in ("token", "invalid", "auth",
                                             "403", "401", "session", "expired")):
                raise KiteSessionExpiredError(
                    f"Kite session expired — please re-link your Kite account. (raw: {e})"
                ) from e
            raise  # re-raise unexpected errors as-is

    def get_ltp(self, instruments: List[str]) -> Dict[str, float]:
        """
        Get Last Traded Price for a list of instruments.
        instruments format: ["NFO:NIFTY24DEC24000CE", ...]
        Returns: {"NFO:NIFTY24DEC24000CE": 152.5, ...}
        Raises KiteSessionExpiredError on auth/permission failure.
        """
        try:
            quote = self.kite.ltp(instruments)
            return {symbol: data["last_price"] for symbol, data in quote.items()}
        except Exception as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("permission", "token", "invalid", "auth",
                                             "403", "401", "session", "expired")):
                raise KiteSessionExpiredError(
                    f"Kite session expired — please re-link your Kite account. (raw: {e})"
                ) from e
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
        Raises KiteHistoricalError on auth failure so callers can surface the real reason.
        """
        try:
            records = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
            )
            return records or []
        except Exception as e:
            err_str = str(e).lower()
            # Surface auth / session failures explicitly so callers can tell the user
            if any(kw in err_str for kw in ("token", "invalid", "auth", "403", "401", "session")):
                raise KiteSessionExpiredError(
                    f"Kite session expired or invalid token — please reconnect Kite. (raw: {e})"
                ) from e
            logger.error(f"Historical data fetch failed for token {instrument_token} [{interval}]: {e}")
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

    def get_all_kite_orders(self) -> List[Dict]:
        """
        Fetch all orders placed today from Kite.
        Used to rebuild the local DB after a wipe.

        Returns a list of Kite order dicts with at minimum:
          order_id, tradingsymbol, transaction_type, quantity,
          average_price / price, status, order_timestamp
        """
        try:
            orders = self.kite.orders()
            return orders or []
        except Exception as e:
            logger.error(f"Kite orders fetch failed: {e}")
            return []

    def get_market_depth(self, tradingsymbol: str) -> Dict:
        """
        Fetch 5-level bid/ask market depth for a single NFO instrument.

        Returns a dict with:
          buy  : list of 5 dicts {price, quantity, orders}  — best bid first
          sell : list of 5 dicts {price, quantity, orders}  — best ask first
          total_buy_qty  : sum of all 5 bid quantities
          total_sell_qty : sum of all 5 ask quantities
          imbalance      : buy_qty / (buy_qty + sell_qty),  range 0.0–1.0
                           > 0.6 = buyer dominated
                           < 0.4 = seller dominated
          spread         : best_ask_price - best_bid_price
          spread_pct     : spread / mid_price * 100

        Returns empty dict on failure (e.g. market closed, API error).
        """
        key = f"NFO:{tradingsymbol}"
        try:
            full_quote = self.kite.quote([key])
            q = full_quote.get(key, {})
            depth = q.get("depth", {})
            buys  = depth.get("buy",  [])
            sells = depth.get("sell", [])

            total_buy  = sum(d.get("quantity", 0) for d in buys)
            total_sell = sum(d.get("quantity", 0) for d in sells)
            total      = total_buy + total_sell

            imbalance = (total_buy / total) if total > 0 else 0.5

            best_bid = buys[0].get("price",  0) if buys  else 0
            best_ask = sells[0].get("price", 0) if sells else 0
            spread = best_ask - best_bid if (best_bid and best_ask) else 0
            mid    = (best_bid + best_ask) / 2 if (best_bid and best_ask) else 0
            spread_pct = (spread / mid * 100) if mid > 0 else 0

            return {
                "buy":            buys,
                "sell":           sells,
                "total_buy_qty":  total_buy,
                "total_sell_qty": total_sell,
                "imbalance":      round(imbalance, 4),
                "best_bid":       best_bid,
                "best_ask":       best_ask,
                "spread":         round(spread, 2),
                "spread_pct":     round(spread_pct, 3),
            }
        except Exception as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("permission", "token", "invalid", "auth",
                                             "403", "401", "session", "expired")):
                raise KiteSessionExpiredError(
                    f"Kite session expired — please re-link your Kite account. (raw: {e})"
                ) from e
            logger.warning(f"Market depth fetch failed for {tradingsymbol}: {e}")
            return {}

    def get_available_cash(self) -> float:
        """
        Convenience helper — returns net available cash as a single float.
        Returns 0.0 on any error so callers don't need try/except.
        """
        try:
            margins = self.kite.margins()
            eq = margins.get("equity", {})
            return float(
                eq.get("net", 0)
                or eq.get("available", {}).get("live_balance", 0)
                or eq.get("available", {}).get("cash", 0)
                or 0
            )
        except Exception as e:
            logger.error(f"Available cash fetch failed: {e}")
            return 0.0


class KiteSessionExpiredError(Exception):
    """Raised when the Kite access token has expired (re-link required)."""
    pass


class KitePermissionError(Exception):
    """
    Raised when the Kite Connect API key lacks permissions for market data
    (ltp / quote / historical data).  This is a SUBSCRIPTION issue — re-linking
    the account will NOT fix it.  The developer must upgrade their Kite Connect
    app plan at https://developers.kite.trade.
    """
    pass
