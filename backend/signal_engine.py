"""
Signal Engine  (v2 — Index-First Architecture)
===============================================
Evaluates an instrument for intraday BUY signal quality.

Philosophy:
  - Never auto-place orders. Produce scored suggestions for human review.
  - A signal is only BUY-worthy when multiple independent conditions agree.
  - Queue tracking rewards persistent, stable signals over one-off noise.

Key changes from v1:
  - Underlying INDEX is ALWAYS the primary data source.
    Option candles have severe issues: premium decay, time-value noise,
    thin liquidity (gaps), and inconsistent OHLCV during low-volume hours.
    RSI/MACD/Price-trend indicators on option candles are unreliable.
  - Pivot level proximity (S1 bounce) is now a scored factor.
  - ATR on INDEX replaces qualitative "stagnant" checks.
  - RSI/MACD/Price-trend all run on index 60-min candles.
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
W_RSI_OVERSOLD   = 20   # RSI below oversold threshold (on INDEX)
W_RSI_RECOVERING = 20   # RSI slope turning up from oversold (momentum reversal)
W_MACD_CROSSOVER = 30   # MACD histogram crossed zero this candle (strongest signal)
W_MACD_MOMENTUM  = 15   # Histogram positive AND increasing (trend continuity)
W_PRICE_TREND    = 15   # Index price above 5-candle mean (short-term uptrend)

# Minimum confidence to emit a BUY suggestion
MIN_BUY_CONFIDENCE = 55


class SignalEngine:
    """
    Evaluates an instrument against RSI + MACD rules on the underlying INDEX,
    persists each evaluation, and provides queue-based prediction.

    The underlying index (NIFTY, BANKNIFTY, etc.) is ALWAYS used as the
    primary OHLCV source.  Option price candles are never used for TA.
    """

    # Shared LLM analyzer — one instance per process (holds the API client)
    _llm = LLMAnalyzer()

    # Minimum candles needed: RSI=14 periods, MACD=26 periods → need ≥27 candles.
    # Index has 420+ hourly candles per 60-day window.
    _MIN_CANDLES = 27

    def __init__(self, kite: KiteService, db: Session):
        self.kite = kite
        self.db = db

    # ──────────────────────────────────────────────────────
    # Historical data helpers
    # ──────────────────────────────────────────────────────

    def _fetch_index_ohlcv(self, underlying_token: int, days: int = 60) -> Optional[object]:
        """
        Fetch hourly OHLCV DataFrame for the underlying INDEX.

        Always fetches the INDEX token, never the option token.
        Index candles are reliable for RSI/MACD — they have continuous data,
        no time-value decay, and consistent liquidity throughout market hours.

        Raises KiteSessionExpiredError if the Kite token is invalid.
        """
        if not TA_AVAILABLE:
            return None
        to_date   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        # KiteSessionExpiredError propagates up — do NOT catch it here
        records = self.kite.get_historical_data(underlying_token, from_date, to_date, "60minute")
        if not records or len(records) < self._MIN_CANDLES:
            logger.warning(
                f"Insufficient index hourly data for token {underlying_token}: "
                f"got {len(records) if records else 0} candles (need ≥{self._MIN_CANDLES})"
            )
            return None
        df = pd.DataFrame(records)
        df.rename(columns={"date": "datetime"}, inplace=True)
        logger.info(f"Index OHLCV fetched: token={underlying_token}, {len(df)} candles")
        return df

    def _fetch_multi_timeframe(self, underlying_token: int) -> Dict:
        """
        Fetch daily (last 30 rows) and weekly (last 20 rows) candles for the
        underlying index.  Used to build multi-TF context for the LLM.

        Returns {"daily": [...], "weekly": [...]} — empty lists on failure.
        """
        result = {"daily": [], "weekly": []}
        if not TA_AVAILABLE:
            return result

        now     = datetime.now()
        to_date = now.strftime("%Y-%m-%d %H:%M:%S")

        # Daily — 90 calendar days ≈ 60 trading sessions
        from_daily = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        daily_raw  = self.kite.get_historical_data(underlying_token, from_daily, to_date, "day")
        if daily_raw:
            result["daily"] = daily_raw[-30:]

        # Weekly — 200 calendar days ≈ 28 weekly bars
        from_weekly = (now - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
        weekly_raw  = self.kite.get_historical_data(underlying_token, from_weekly, to_date, "week")
        if weekly_raw:
            result["weekly"] = weekly_raw[-20:]

        logger.info(
            f"Multi-TF index data: token={underlying_token} "
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

            # Pivot points from yesterday's daily candle
            prev    = daily_rows[-2] if len(daily_rows) >= 2 else daily_rows[-1]
            H, L, C = prev["high"], prev["low"], prev["close"]
            PP       = (H + L + C) / 3
            R1       = 2 * PP - L
            R2       = PP + (H - L)
            S1       = 2 * PP - H
            S2       = PP - (H - L)

            # Swing highs / lows (last 10 sessions, look-back=2)
            window      = min(10, len(daily_rows))
            recent_h    = highs[-window:]
            recent_l    = lows[-window:]
            swing_highs = []
            swing_lows  = []
            for i in range(2, window - 2):
                if recent_h[i] == max(recent_h[i-2:i+3]):
                    swing_highs.append(round(recent_h[i], 2))
                if recent_l[i] == min(recent_l[i-2:i+3]):
                    swing_lows.append(round(recent_l[i], 2))

            # Moving averages
            ma20 = round(sum(closes[-20:]) / min(20, len(closes)), 2) if len(closes) >= 5 else None
            ma50 = round(sum(closes[-50:]) / min(50, len(closes)), 2) if len(closes) >= 5 else None

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
    # Indicator computation (always on INDEX candles)
    # ──────────────────────────────────────────────────────

    def _compute_indicators(self, df: object) -> Dict:
        """
        Compute RSI, MACD, EMA, and price trend from the INDEX DataFrame.
        All indicators reflect the health of the underlying market, not the option.
        Raises ValueError if insufficient data.
        """
        closes = df["close"]

        # RSI (14-period) on index closes
        rsi_series = ta.momentum.RSIIndicator(closes, window=14).rsi()
        rsi_now  = float(rsi_series.iloc[-1])
        rsi_prev = float(rsi_series.iloc[-2])

        # MACD (12, 26, 9) on index closes
        macd_obj    = ta.trend.MACD(closes, window_fast=12, window_slow=26, window_sign=9)
        hist_now    = float(macd_obj.macd_diff().iloc[-1])
        hist_prev   = float(macd_obj.macd_diff().iloc[-2])
        macd_line   = float(macd_obj.macd().iloc[-1])
        signal_line = float(macd_obj.macd_signal().iloc[-1])

        # EMA crossover context (9 vs 21)
        ema9  = ta.trend.EMAIndicator(closes, window=9).ema_indicator()
        ema21 = ta.trend.EMAIndicator(closes, window=21).ema_indicator()
        ema9_now  = float(ema9.iloc[-1])
        ema21_now = float(ema21.iloc[-1])
        ema_bullish = ema9_now > ema21_now

        # Short-term price trend: % change over last 6 candles (on INDEX)
        price_now  = float(closes.iloc[-1])
        price_6ago = float(closes.iloc[-6]) if len(closes) >= 6 else price_now
        price_trend_pct = (price_now - price_6ago) / price_6ago * 100

        # Pivot proximity (approximate — exact pivot scoring in S/R)
        # This gives a quick check: is index near a support level?
        price_5ago_high = float(df["high"].iloc[-6:-1].max()) if len(df) >= 6 else price_now
        price_5ago_low  = float(df["low"].iloc[-6:-1].min()) if len(df) >= 6 else price_now
        near_support = price_now <= price_5ago_low * 1.005  # within 0.5% of recent 5-bar low

        return {
            "rsi":                rsi_now,
            "rsi_prev":           rsi_prev,
            "macd_histogram":     hist_now,
            "macd_histogram_prev": hist_prev,
            "macd_line":          macd_line,
            "signal_line":        signal_line,
            "current_price":      price_now,
            "price_trend_pct":    price_trend_pct,
            "ema9":               ema9_now,
            "ema21":              ema21_now,
            "ema_bullish":        ema_bullish,
            "near_support":       near_support,
        }

    # ──────────────────────────────────────────────────────
    # Strengthened BUY conditions with scoring (on INDEX)
    # ──────────────────────────────────────────────────────

    def _score_buy(self, ind: Dict, rsi_oversold: int = 30) -> Tuple[float, List[str]]:
        """
        Returns (confidence_score 0–100, reasons list).
        All signals are from the INDEX — no option price candles.
        Each condition contributes a weighted score; partial credit for near misses.
        """
        score = 0.0
        reasons = []

        rsi      = ind["rsi"]
        rsi_prev = ind["rsi_prev"]
        hist     = ind["macd_histogram"]
        hist_p   = ind["macd_histogram_prev"]
        trend    = ind["price_trend_pct"]
        ema_bull = ind.get("ema_bullish", False)
        near_sup = ind.get("near_support", False)

        # 1. RSI oversold on INDEX (hard gate: must be < oversold threshold)
        if rsi <= rsi_oversold:
            score += W_RSI_OVERSOLD
            reasons.append(f"Index RSI oversold: {rsi:.1f} ≤ {rsi_oversold}")
        elif rsi <= rsi_oversold + 5:
            score += W_RSI_OVERSOLD * 0.5
            reasons.append(f"Index RSI near oversold: {rsi:.1f}")
        else:
            reasons.append(f"Index RSI not oversold: {rsi:.1f} (need ≤ {rsi_oversold})")

        # 2. RSI momentum reversal on INDEX (slope turning up from oversold region)
        if rsi <= rsi_oversold + 10 and rsi > rsi_prev:
            slope = rsi - rsi_prev
            score += W_RSI_RECOVERING if slope >= 1.0 else W_RSI_RECOVERING * 0.5
            reasons.append(f"Index RSI recovering: {rsi_prev:.1f} → {rsi:.1f} (+{slope:.1f})")
        else:
            reasons.append(f"Index RSI not recovering (slope: {rsi - rsi_prev:+.1f})")

        # 3. MACD fresh crossover on INDEX (histogram flipped + this candle)
        if hist > 0 and hist_p <= 0:
            score += W_MACD_CROSSOVER
            reasons.append(f"Index MACD bullish crossover: hist {hist_p:.4f} → {hist:.4f}")
        elif hist > 0 and hist_p > 0:
            score += W_MACD_CROSSOVER * 0.4
            reasons.append(f"Index MACD histogram positive (ongoing): {hist:.4f}")
        else:
            reasons.append(f"Index MACD histogram negative: {hist:.4f}")

        # 4. MACD momentum on INDEX — histogram increasing
        if hist > hist_p and hist > 0:
            score += W_MACD_MOMENTUM
            reasons.append(f"Index MACD momentum increasing: {hist_p:.4f} → {hist:.4f}")
        elif hist > hist_p:
            score += W_MACD_MOMENTUM * 0.4
            reasons.append(f"Index MACD histogram rising (still negative): {hist:.4f}")
        else:
            reasons.append(f"Index MACD histogram declining: {hist:.4f}")

        # 5. Short-term index price trend + EMA structure
        # EMA bullish (EMA9 > EMA21) gives partial bonus even if price trend is modest
        if trend > 0.5:
            score += W_PRICE_TREND
            reasons.append(f"Index uptrend: +{trend:.2f}% over 6 candles")
        elif trend > 0 and ema_bull:
            score += W_PRICE_TREND * 0.65
            reasons.append(f"Index slight uptrend +{trend:.2f}%, EMA9>EMA21 confirms")
        elif trend > 0:
            score += W_PRICE_TREND * 0.4
            reasons.append(f"Index slightly up: +{trend:.2f}%")
        elif ema_bull and near_sup:
            # Index at support with EMA still bullish — potential reversal
            score += W_PRICE_TREND * 0.25
            reasons.append(f"Index at support, EMA9>EMA21 (potential reversal setup)")
        else:
            reasons.append(f"Index trend negative: {trend:.2f}%")

        return round(score, 1), reasons

    # ──────────────────────────────────────────────────────
    # Queue-based prediction
    # ──────────────────────────────────────────────────────

    def _queue_confidence_boost(
        self, instrument: str, user_id: int, direction: str
    ) -> Tuple[float, str]:
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

        IMPORTANT: The underlying INDEX is always the primary data source.
        RSI, MACD, EMA, and price-trend are computed from index candles only.
        Option candles are not used for any TA indicators.

        If underlying_token is not provided, we still try the instrument_token as
        a last resort (may happen for non-option instruments or direct index queries).
        """
        # ── Determine the token to use for TA ─────────────────────────────────
        # ALWAYS prefer the underlying index token
        index_token  = underlying_token if underlying_token else instrument_token
        session_error_msg = None
        data_source   = "underlying index"

        try:
            df = self._fetch_index_ohlcv(index_token)
        except KiteSessionExpiredError as exc:
            session_error_msg = str(exc)
            df = None

        # If we had no explicit underlying_token and the direct token failed,
        # that's the only token we have — no fallback possible
        if df is None:
            if session_error_msg:
                reason = "Kite session expired — please reconnect Kite (Dashboard → Connect Kite)"
            else:
                reason = (
                    "No hourly data for the underlying index. "
                    "If market is closed, data will be available on the next trading day."
                )
            return {
                "instrument":     tradingsymbol,
                "direction":      "HOLD",
                "confidence":     0,
                "reasons":        [reason],
                "current_price":  None,
                "suggested_entry": None,
                "suggested_sl":   None,
                "data_source":    data_source,
                "session_expired": bool(session_error_msg),
            }

        try:
            ind = self._compute_indicators(df)
        except Exception as e:
            logger.error(f"Indicator computation failed for {tradingsymbol}: {e}")
            return {
                "instrument":     tradingsymbol,
                "direction":      "HOLD",
                "confidence":     0,
                "reasons":        [f"Indicator error: {e}"],
                "current_price":  None,
                "suggested_entry": None,
                "suggested_sl":   None,
            }

        base_score, reasons = self._score_buy(ind, rsi_oversold)

        # ── Multi-timeframe + Support/Resistance ──────────────────────────────
        # Always fetch MTF for the index token
        mtf_data  = self._fetch_multi_timeframe(index_token)
        sr_levels = self._compute_support_resistance(mtf_data["daily"])

        # ── Check pivot proximity and add as reason ───────────────────────────
        if sr_levels:
            spot = ind.get("current_price", 0)
            s1   = sr_levels.get("pivot_s1")
            pp   = sr_levels.get("pivot_pp")
            r1   = sr_levels.get("pivot_r1")
            if s1 and pp and r1 and spot > 0:
                dist_s1 = abs(spot - s1) / s1 * 100
                if dist_s1 <= 0.3:
                    reasons.append(f"Index near S1 pivot support {s1:.0f} ({dist_s1:.2f}% away) — high-prob CE entry")
                    base_score = min(100.0, base_score + 8)   # pivot bonus
                elif dist_s1 <= 0.7:
                    reasons.append(f"Index approaching S1 pivot {s1:.0f} ({dist_s1:.2f}% away)")
                    base_score = min(100.0, base_score + 4)
                elif s1 <= spot <= pp:
                    reasons.append(f"Index in S1–PP support zone ({s1:.0f}–{pp:.0f})")
                    base_score = min(100.0, base_score + 2)
                elif spot >= r1 * 0.998:
                    reasons.append(f"⚠ Index at/above R1 resistance {r1:.0f} — risky CE entry")
                    base_score = max(0.0, base_score - 5)

        # ── LLM analysis ──────────────────────────────────────────────────────
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

        # Blend: 40% rule-based + 60% LLM (LLM has broader multi-TF context)
        if llm_result["available"] and llm_confidence > 0:
            blended_score = round(0.40 * base_score + 0.60 * llm_confidence, 1)
            reasons.append(
                f"LLM ({llm_direction} {llm_confidence}%): "
                + " | ".join(llm_reasons[:2])
            )
        else:
            blended_score = base_score
            reasons.append("LLM: unavailable — using rule-based score only")

        # Direction from blended score
        direction = "BUY" if blended_score >= MIN_BUY_CONFIDENCE else "HOLD"

        # Queue boost / penalty
        boost, queue_reason = self._queue_confidence_boost(tradingsymbol, user_id, direction)
        reasons.append(f"Queue: {queue_reason}")
        final_score = max(0.0, min(100.0, blended_score + boost))

        # Re-evaluate direction after queue adjustment
        direction = "BUY" if final_score >= MIN_BUY_CONFIDENCE else "HOLD"

        # Suggested prices (using index current price as proxy for option entry signal)
        price        = ind["current_price"]
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
            f"Signal [{tradingsymbol}] (index token={index_token}): "
            f"{direction} confidence={final_score:.1f}% "
            f"RSI={ind['rsi']:.1f} MACD_hist={ind['macd_histogram']:.4f} "
            f"data_source={data_source}"
        )

        return {
            "instrument":       tradingsymbol,
            "instrument_token": instrument_token,
            "direction":        direction,
            "confidence":       final_score,
            "reasons":          reasons,
            "indicators": {
                "rsi":                round(ind["rsi"], 2),
                "rsi_prev":           round(ind["rsi_prev"], 2),
                "macd_histogram":     round(ind["macd_histogram"], 4),
                "macd_histogram_prev": round(ind["macd_histogram_prev"], 4),
                "macd_line":          round(ind["macd_line"], 4),
                "signal_line":        round(ind["signal_line"], 4),
                "price_trend_pct":    round(ind["price_trend_pct"], 3),
                "ema9":               round(ind.get("ema9", 0), 2),
                "ema21":              round(ind.get("ema21", 0), 2),
                "ema_bullish":        ind.get("ema_bullish", False),
                "near_support":       ind.get("near_support", False),
            },
            "current_price":    round(price, 2),
            "suggested_entry":  round(price, 2),
            "suggested_sl":     suggested_sl,
            "llm_analysis": {
                "direction":   llm_result["direction"],
                "confidence":  llm_result["confidence"],
                "reasoning":   llm_result["reasoning"],
                "available":   llm_result["available"],
            },
            "rule_score":         round(base_score, 1),
            "blended_score":      blended_score,
            "support_resistance": sr_levels,
            "data_source":        data_source,
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
                "direction":    r.direction,
                "confidence":   r.confidence,
                "rsi":          r.rsi,
                "macd_histogram": r.macd_histogram,
                "current_price":  r.current_price,
                "reasons":      json.loads(r.reasons) if r.reasons else [],
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
        streak     = 0
        for r in records:
            if r.direction == streak_dir:
                streak += 1
            else:
                break

        # Confidence trend
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
            "instrument":    instrument,
            "prediction":    prediction,
            "confidence":    round(avg_conf, 1),
            "signal_count":  len(records),
            "buy_signals":   buy_count,
            "hold_signals":  hold_count,
            "current_streak": {"direction": streak_dir, "count": streak},
            "confidence_trend": trend,
            "latest_rsi":            records[0].rsi,
            "latest_macd_histogram": records[0].macd_histogram,
            "latest_price":          records[0].current_price,
            "summary": (
                f"{buy_count}/{len(records)} signals are BUY, "
                f"avg confidence {avg_conf:.1f}%, "
                f"streak of {streak} {streak_dir}, "
                f"confidence trend {trend}."
            ),
        }
