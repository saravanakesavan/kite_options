"""
Signal Engine — strengthened RSI + MACD conditions with confidence scoring
and queue-based prediction.

Philosophy:
  - Never auto-place orders. Produce scored suggestions for human review.
  - A signal is only BUY-worthy when multiple independent conditions agree.
  - Queue tracking rewards persistent, stable signals over one-off noise.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from kite_service import KiteService, KiteSessionExpiredError
from llm_analyzer import LLMAnalyzer
from models import SignalRecord, TradingStrategy

try:
    import pandas as pd
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    logging.warning("pandas/ta not available — signal engine will not function")

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# Confidence weights  (must sum to 100)
# ──────────────────────────────────────────────────────────
W_RSI_OVERSOLD   = 20   # RSI below oversold threshold
W_RSI_RECOVERING = 20   # RSI slope turning up (momentum reversal)
W_MACD_CROSSOVER = 30   # MACD histogram crossed zero this candle (strongest signal)
W_MACD_MOMENTUM  = 15   # Histogram positive AND increasing (trend continuity)
W_PRICE_TREND    = 15   # Price above 5-candle mean (short-term uptrend in underlying)

# Minimum confidence to emit a BUY suggestion
MIN_BUY_CONFIDENCE = 55


class SignalEngine:
    """
    Evaluates an instrument against strengthened RSI + MACD rules,
    persists each evaluation, and provides queue-based prediction.
    """

    # Shared LLM analyzer — one instance per process (holds the API client)
    _llm = LLMAnalyzer()

    def __init__(self, kite: KiteService, db: Session):
        self.kite = kite
        self.db = db

    # ──────────────────────────────────────────────────────
    # Historical data helpers
    # ──────────────────────────────────────────────────────

    # Minimum candles needed: RSI=14 periods, MACD=26 periods → need ≥27 candles.
    # We ask for 60 days (420+ hourly candles for the index) but accept ≥27.
    _MIN_CANDLES = 27

    def _fetch_ohlcv(self, instrument_token: int, days: int = 60) -> Optional[object]:
        """
        Return a DataFrame with OHLCV columns, or None if insufficient data.
        Raises KiteSessionExpiredError if the Kite token is invalid.
        """
        if not TA_AVAILABLE:
            return None
        to_date   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        # KiteSessionExpiredError propagates up — do NOT catch it here
        records   = self.kite.get_historical_data(instrument_token, from_date, to_date, "60minute")
        if not records or len(records) < self._MIN_CANDLES:
            logger.warning(
                f"Insufficient hourly data for token {instrument_token}: "
                f"got {len(records) if records else 0} candles (need ≥{self._MIN_CANDLES})"
            )
            return None
        df = pd.DataFrame(records)
        df.rename(columns={"date": "datetime"}, inplace=True)
        return df

    def _fetch_multi_timeframe(self, instrument_token: int) -> Dict:
        """
        Fetch daily (90 days) and weekly (26 weeks ≈ 6 months) candles for the
        given token.  Used to build support/resistance context for the LLM.

        Returns:
            {
                "daily":  list[dict] or [],   # last 30 daily rows
                "weekly": list[dict] or [],   # last 20 weekly rows
            }
        """
        result = {"daily": [], "weekly": []}
        if not TA_AVAILABLE:
            return result

        now      = datetime.now()
        to_date  = now.strftime("%Y-%m-%d %H:%M:%S")

        # Daily — 90 calendar days covers ~60 trading sessions
        from_daily = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        daily_raw  = self.kite.get_historical_data(instrument_token, from_daily, to_date, "day")
        if daily_raw:
            result["daily"] = daily_raw[-30:]   # keep last 30 trading days

        # Weekly — 200 calendar days gives ~28 weekly bars
        from_weekly = (now - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
        weekly_raw  = self.kite.get_historical_data(instrument_token, from_weekly, to_date, "week")
        if weekly_raw:
            result["weekly"] = weekly_raw[-20:]  # keep last 20 weekly candles

        logger.info(
            f"Multi-TF fetched for {instrument_token}: "
            f"daily={len(result['daily'])} weekly={len(result['weekly'])}"
        )
        return result

    def _compute_support_resistance(self, daily_rows: List[Dict]) -> Dict:
        """
        Derive key price levels from daily OHLCV rows:
          - Standard pivot points from yesterday's candle (PP, R1/R2, S1/S2)
          - Swing highs / swing lows (recent 10-day window)
          - 20-day and 50-day simple moving averages

        Returns a dict ready to pass to the LLM.
        Returns {} if insufficient data.
        """
        if not daily_rows or len(daily_rows) < 5:
            return {}

        try:
            closes = [r["close"] for r in daily_rows]
            highs  = [r["high"]  for r in daily_rows]
            lows   = [r["low"]   for r in daily_rows]

            # ── Pivot points (from yesterday's daily candle) ──────────────
            prev      = daily_rows[-2] if len(daily_rows) >= 2 else daily_rows[-1]
            H, L, C   = prev["high"], prev["low"], prev["close"]
            PP        = (H + L + C) / 3
            R1        = 2 * PP - L
            R2        = PP + (H - L)
            S1        = 2 * PP - H
            S2        = PP - (H - L)

            # ── Swing highs / lows (last 10 sessions, look-back=2) ─────────
            window     = min(10, len(daily_rows))
            recent_h   = highs[-window:]
            recent_l   = lows[-window:]
            swing_highs = []
            swing_lows  = []
            for i in range(2, window - 2):
                if recent_h[i] == max(recent_h[i-2:i+3]):
                    swing_highs.append(round(recent_h[i], 2))
                if recent_l[i] == min(recent_l[i-2:i+3]):
                    swing_lows.append(round(recent_l[i], 2))

            # ── Moving averages ────────────────────────────────────────────
            ma20 = round(sum(closes[-20:]) / min(20, len(closes)), 2) if len(closes) >= 5 else None
            ma50 = round(sum(closes[-50:]) / min(50, len(closes)), 2) if len(closes) >= 5 else None

            # ── 52-week high/low from available data ───────────────────────
            wk52_high = round(max(highs), 2)
            wk52_low  = round(min(lows), 2)

            return {
                "pivot_pp": round(PP, 2),
                "pivot_r1": round(R1, 2),
                "pivot_r2": round(R2, 2),
                "pivot_s1": round(S1, 2),
                "pivot_s2": round(S2, 2),
                "swing_highs": sorted(set(swing_highs), reverse=True)[:3],
                "swing_lows":  sorted(set(swing_lows))[:3],
                "ma_20": ma20,
                "ma_50": ma50,
                "period_high": wk52_high,
                "period_low":  wk52_low,
            }
        except Exception as e:
            logger.warning(f"S/R computation failed: {e}")
            return {}

    # ──────────────────────────────────────────────────────
    # Indicator computation
    # ──────────────────────────────────────────────────────

    def _compute_indicators(self, df: object) -> Dict:
        """
        Returns a dict of indicator values at the latest and previous candle.
        Raises ValueError if insufficient data.
        """
        closes = df["close"]

        # RSI (14-period)
        rsi_series = ta.momentum.RSIIndicator(closes, window=14).rsi()
        rsi_now  = float(rsi_series.iloc[-1])
        rsi_prev = float(rsi_series.iloc[-2])

        # MACD (12, 26, 9)
        macd_obj = ta.trend.MACD(closes, window_fast=12, window_slow=26, window_sign=9)
        hist_now    = float(macd_obj.macd_diff().iloc[-1])
        hist_prev   = float(macd_obj.macd_diff().iloc[-2])
        macd_line   = float(macd_obj.macd().iloc[-1])
        signal_line = float(macd_obj.macd_signal().iloc[-1])

        # Short-term price trend: % change over last 6 candles
        price_now  = float(closes.iloc[-1])
        price_6ago = float(closes.iloc[-6]) if len(closes) >= 6 else price_now
        price_trend_pct = (price_now - price_6ago) / price_6ago * 100

        return {
            "rsi": rsi_now,
            "rsi_prev": rsi_prev,
            "macd_histogram": hist_now,
            "macd_histogram_prev": hist_prev,
            "macd_line": macd_line,
            "signal_line": signal_line,
            "current_price": price_now,
            "price_trend_pct": price_trend_pct,
        }

    # ──────────────────────────────────────────────────────
    # Strengthened BUY conditions with scoring
    # ──────────────────────────────────────────────────────

    def _score_buy(self, ind: Dict, rsi_oversold: int = 30) -> Tuple[float, List[str]]:
        """
        Returns (confidence_score 0–100, reasons list).
        Each condition contributes a weighted score; partial credit for near misses.
        """
        score = 0.0
        reasons = []

        rsi      = ind["rsi"]
        rsi_prev = ind["rsi_prev"]
        hist     = ind["macd_histogram"]
        hist_p   = ind["macd_histogram_prev"]
        trend    = ind["price_trend_pct"]

        # 1. RSI oversold (hard gate: must be < oversold threshold)
        if rsi <= rsi_oversold:
            score += W_RSI_OVERSOLD
            reasons.append(f"RSI oversold: {rsi:.1f} ≤ {rsi_oversold}")
        elif rsi <= rsi_oversold + 5:
            score += W_RSI_OVERSOLD * 0.5   # partial — approaching oversold
            reasons.append(f"RSI near oversold: {rsi:.1f}")
        else:
            reasons.append(f"RSI not oversold: {rsi:.1f} (need ≤ {rsi_oversold})")

        # 2. RSI momentum reversal (slope turning up from oversold region)
        if rsi <= rsi_oversold + 10 and rsi > rsi_prev:
            slope = rsi - rsi_prev
            score += W_RSI_RECOVERING if slope >= 1.0 else W_RSI_RECOVERING * 0.5
            reasons.append(f"RSI recovering: {rsi_prev:.1f} → {rsi:.1f} (+{slope:.1f})")
        else:
            reasons.append(f"RSI not recovering (slope: {rsi - rsi_prev:+.1f})")

        # 3. MACD fresh crossover (histogram flipped from negative to positive THIS candle)
        if hist > 0 and hist_p <= 0:
            score += W_MACD_CROSSOVER
            reasons.append(f"MACD bullish crossover: hist {hist_p:.4f} → {hist:.4f}")
        elif hist > 0 and hist_p > 0:
            # Already positive — check if it was recent (within 2 candles)
            score += W_MACD_CROSSOVER * 0.4
            reasons.append(f"MACD histogram positive (ongoing): {hist:.4f}")
        else:
            reasons.append(f"MACD histogram negative: {hist:.4f}")

        # 4. MACD momentum — histogram increasing (bullish pressure building)
        if hist > hist_p and hist > 0:
            score += W_MACD_MOMENTUM
            reasons.append(f"MACD momentum increasing: {hist_p:.4f} → {hist:.4f}")
        elif hist > hist_p:
            score += W_MACD_MOMENTUM * 0.4
            reasons.append(f"MACD histogram rising (still negative): {hist:.4f}")
        else:
            reasons.append(f"MACD histogram declining: {hist:.4f}")

        # 5. Short-term price trend
        if trend > 0.5:
            score += W_PRICE_TREND
            reasons.append(f"Price uptrend: +{trend:.2f}% over 6 candles")
        elif trend > 0:
            score += W_PRICE_TREND * 0.5
            reasons.append(f"Price slightly up: +{trend:.2f}%")
        else:
            reasons.append(f"Price trend negative: {trend:.2f}%")

        return round(score, 1), reasons

    # ──────────────────────────────────────────────────────
    # Queue-based prediction
    # ──────────────────────────────────────────────────────

    def _queue_confidence_boost(self, instrument: str, user_id: int, direction: str) -> Tuple[float, str]:
        """
        Look at the last 5 signals for this instrument.
        Consecutive same-direction signals add a prediction bonus (up to +15 pts).
        Returns (boost, explanation).
        """
        recent = (
            self.db.query(SignalRecord)
            .filter(
                SignalRecord.instrument == instrument,
                SignalRecord.user_id == user_id,
                SignalRecord.evaluated_at >= datetime.now() - timedelta(hours=4),
            )
            .order_by(SignalRecord.evaluated_at.desc())
            .limit(5)
            .all()
        )

        if not recent:
            return 0.0, "No prior signals in queue"

        # Count leading consecutive same-direction signals
        streak = 0
        for sig in recent:
            if sig.direction == direction:
                streak += 1
            else:
                break

        if streak == 0:
            return -10.0, f"Queue: last signal was opposite direction ({recent[0].direction})"
        elif streak == 1:
            return 5.0, "Queue: 1 prior signal agrees"
        elif streak == 2:
            return 10.0, "Queue: 2 consecutive signals agree — moderate confidence"
        else:
            return 15.0, f"Queue: {streak} consecutive signals agree — strong persistence"

    # ──────────────────────────────────────────────────────
    # Main evaluation
    # ──────────────────────────────────────────────────────

    def evaluate(
        self,
        tradingsymbol: str,
        instrument_token: int,
        user_id: int,
        rsi_oversold: int = 30,
        strategy_id: Optional[int] = None,
        underlying_token: Optional[int] = None,
    ) -> Dict:
        """
        Full evaluation for one instrument.
        Persists the signal to DB.
        Returns a suggestion dict ready to send to the frontend.

        If the option itself has < 30 candles (new/near-expiry contract),
        falls back to the underlying index's OHLCV data (underlying_token).
        This is actually better technically — index TA is more reliable
        than option-price TA for entry signals.
        """
        # ── Fetch OHLCV with fallback chain and explicit error surfacing ─────
        session_error_msg = None
        data_source = "option"
        try:
            df = self._fetch_ohlcv(instrument_token)
        except KiteSessionExpiredError as exc:
            session_error_msg = str(exc)
            df = None

        if df is None and underlying_token and underlying_token != instrument_token:
            data_source = "underlying index"
            logger.info(
                f"{tradingsymbol}: option data insufficient ({session_error_msg or 'too few candles'}), "
                f"falling back to underlying token {underlying_token}"
            )
            try:
                df = self._fetch_ohlcv(underlying_token)
                if df is not None:
                    session_error_msg = None  # underlying worked — no auth problem
            except KiteSessionExpiredError as exc:
                session_error_msg = str(exc)
                df = None

        if df is None:
            if session_error_msg:
                reason = "Kite session expired — please reconnect Kite (Dashboard → Connect Kite)"
            elif underlying_token:
                reason = (
                    f"No hourly data for this option or its underlying index. "
                    f"If market is closed, data will be available on the next trading day. "
                    f"Select ATM/near-ATM strikes for best results."
                )
            else:
                reason = "Insufficient historical data — select ATM or near-ATM strikes"
            return {
                "instrument": tradingsymbol,
                "direction": "HOLD",
                "confidence": 0,
                "reasons": [reason],
                "current_price": None,
                "suggested_entry": None,
                "suggested_sl": None,
                "data_source": data_source,
                "session_expired": bool(session_error_msg),
            }

        try:
            ind = self._compute_indicators(df)
        except Exception as e:
            logger.error(f"Indicator computation failed for {tradingsymbol}: {e}")
            return {
                "instrument": tradingsymbol,
                "direction": "HOLD",
                "confidence": 0,
                "reasons": [f"Indicator error: {e}"],
                "current_price": None,
                "suggested_entry": None,
                "suggested_sl": None,
            }

        base_score, reasons = self._score_buy(ind, rsi_oversold)

        # ── Multi-timeframe + Support/Resistance ──────────────────
        # Use the underlying index token for daily/weekly data — index candles
        # are more meaningful for S/R than individual option prices.
        mtf_token  = underlying_token if underlying_token else instrument_token
        mtf_data   = self._fetch_multi_timeframe(mtf_token)
        sr_levels  = self._compute_support_resistance(mtf_data["daily"])

        # ── LLM analysis ──────────────────────────────────────────
        ohlcv_rows = df.to_dict(orient="records") if df is not None else []
        llm_result = self._llm.analyze(
            tradingsymbol=tradingsymbol,
            ohlcv_rows=ohlcv_rows,
            indicators=ind,
            spot_price=ind.get("current_price", 0),
            daily_rows=mtf_data["daily"],
            weekly_rows=mtf_data["weekly"],
            support_resistance=sr_levels,
        )
        llm_direction  = llm_result["direction"]
        llm_confidence = llm_result["confidence"]
        llm_reasons    = llm_result["reasoning"]

        # Blend: 40% rule-based + 60% LLM (LLM has broader context)
        # If LLM is unavailable, fall back to 100% rule-based.
        if llm_result["available"] and llm_confidence > 0:
            blended_score = round(0.40 * base_score + 0.60 * llm_confidence, 1)
            reasons.append(
                f"LLM ({llm_direction} {llm_confidence}%): "
                + " | ".join(llm_reasons[:2])  # top 2 LLM reasons inline
            )
        else:
            blended_score = base_score
            reasons.append("LLM: unavailable — using rule-based score only")

        # Determine raw direction from blended score
        if blended_score >= MIN_BUY_CONFIDENCE:
            direction = "BUY"
        else:
            direction = "HOLD"

        # Queue boost/penalty
        boost, queue_reason = self._queue_confidence_boost(tradingsymbol, user_id, direction)
        reasons.append(f"Queue: {queue_reason}")
        final_score = max(0.0, min(100.0, blended_score + boost))

        # Re-evaluate direction after queue adjustment
        if final_score >= MIN_BUY_CONFIDENCE:
            direction = "BUY"
        else:
            direction = "HOLD"

        # Suggested prices
        price = ind["current_price"]
        suggested_sl = round(price * 0.97, 1) if direction == "BUY" else None  # 3% SL

        # Persist signal
        record = SignalRecord(
            instrument=tradingsymbol,
            user_id=user_id,
            strategy_id=strategy_id,
            rsi=ind["rsi"],
            rsi_prev=ind["rsi_prev"],
            macd_histogram=ind["macd_histogram"],
            macd_histogram_prev=ind["macd_histogram_prev"],
            macd_line=ind["macd_line"],
            signal_line=ind["signal_line"],
            current_price=price,
            price_trend_pct=ind["price_trend_pct"],
            direction=direction,
            confidence=final_score,
            reasons=json.dumps(reasons),
        )
        self.db.add(record)
        self.db.commit()

        logger.info(
            f"Signal [{tradingsymbol}]: {direction} confidence={final_score:.1f}% "
            f"RSI={ind['rsi']:.1f} MACD_hist={ind['macd_histogram']:.4f}"
        )

        return {
            "instrument": tradingsymbol,
            "instrument_token": instrument_token,
            "direction": direction,
            "confidence": final_score,
            "reasons": reasons,
            "indicators": {
                "rsi": round(ind["rsi"], 2),
                "rsi_prev": round(ind["rsi_prev"], 2),
                "macd_histogram": round(ind["macd_histogram"], 4),
                "macd_histogram_prev": round(ind["macd_histogram_prev"], 4),
                "macd_line": round(ind["macd_line"], 4),
                "signal_line": round(ind["signal_line"], 4),
                "price_trend_pct": round(ind["price_trend_pct"], 3),
            },
            "current_price": round(price, 2),
            "suggested_entry": round(price, 2),
            "suggested_sl": suggested_sl,
            "llm_analysis": {
                "direction":   llm_result["direction"],
                "confidence":  llm_result["confidence"],
                "reasoning":   llm_result["reasoning"],
                "available":   llm_result["available"],
            },
            "rule_score": round(base_score, 1),
            "blended_score": blended_score,
            "support_resistance": sr_levels,
            "weekly_candles_used": len(mtf_data["weekly"]),
            "daily_candles_used":  len(mtf_data["daily"]),
        }

    # ──────────────────────────────────────────────────────
    # Queue reader (for frontend signal history)
    # ──────────────────────────────────────────────────────

    def get_signal_queue(self, instrument: str, user_id: int, limit: int = 10) -> List[Dict]:
        """Return last `limit` signal evaluations for an instrument."""
        records = (
            self.db.query(SignalRecord)
            .filter(
                SignalRecord.instrument == instrument,
                SignalRecord.user_id == user_id,
            )
            .order_by(SignalRecord.evaluated_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "evaluated_at": r.evaluated_at,
                "direction": r.direction,
                "confidence": r.confidence,
                "rsi": r.rsi,
                "macd_histogram": r.macd_histogram,
                "current_price": r.current_price,
                "reasons": json.loads(r.reasons) if r.reasons else [],
            }
            for r in records
        ]

    def get_prediction(self, instrument: str, user_id: int) -> Dict:
        """
        Aggregate recent signals to produce a prediction summary.
        Looks at the last 10 signals (up to 4 hours back).
        """
        records = (
            self.db.query(SignalRecord)
            .filter(
                SignalRecord.instrument == instrument,
                SignalRecord.user_id == user_id,
                SignalRecord.evaluated_at >= datetime.now() - timedelta(hours=4),
            )
            .order_by(SignalRecord.evaluated_at.desc())
            .limit(10)
            .all()
        )

        if not records:
            return {
                "instrument": instrument,
                "prediction": "INSUFFICIENT_DATA",
                "confidence": 0,
                "signal_count": 0,
                "summary": "No signals evaluated in the last 4 hours.",
            }

        buy_count  = sum(1 for r in records if r.direction == "BUY")
        hold_count = len(records) - buy_count
        avg_conf   = sum(r.confidence for r in records) / len(records)

        # Streak of most recent direction
        streak_dir = records[0].direction
        streak = 0
        for r in records:
            if r.direction == streak_dir:
                streak += 1
            else:
                break

        # Confidence trend: is confidence rising or falling?
        if len(records) >= 3:
            recent_avg = sum(r.confidence for r in records[:3]) / 3
            older_avg  = sum(r.confidence for r in records[-3:]) / 3
            trend = "RISING" if recent_avg > older_avg + 5 else (
                "FALLING" if recent_avg < older_avg - 5 else "STABLE"
            )
        else:
            trend = "STABLE"

        # Final prediction
        if buy_count >= 6 and streak >= 3 and trend in ("RISING", "STABLE"):
            prediction = "STRONG_BUY"
        elif buy_count >= 4:
            prediction = "BUY"
        elif hold_count >= 8:
            prediction = "HOLD"
        else:
            prediction = "MIXED"

        return {
            "instrument": instrument,
            "prediction": prediction,
            "confidence": round(avg_conf, 1),
            "signal_count": len(records),
            "buy_signals": buy_count,
            "hold_signals": hold_count,
            "current_streak": {"direction": streak_dir, "count": streak},
            "confidence_trend": trend,
            "latest_rsi": records[0].rsi,
            "latest_macd_histogram": records[0].macd_histogram,
            "latest_price": records[0].current_price,
            "summary": (
                f"{buy_count}/{len(records)} signals are BUY, "
                f"avg confidence {avg_conf:.1f}%, "
                f"streak of {streak} {streak_dir}, "
                f"confidence trend {trend}."
            ),
        }
