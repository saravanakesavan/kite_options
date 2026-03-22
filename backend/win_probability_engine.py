"""
Win Probability Engine
======================
Ranks NFO call-option instruments by their probability of generating
an intraday profit.  Uses 11 independent factors (weights sum to 100):

  ── Technical Analysis (index OHLCV, 60-day hourly) ──────────────────────
  1.  RSI oversold + reversal slope    12 pts
  2.  MACD crossover + histogram        18 pts
  3.  Bollinger Band position            8 pts
  4.  EMA 9 > EMA 21 trend              8 pts
  5.  ATR volatility window              4 pts

  ── Live Option Quote ─────────────────────────────────────────────────────
  6.  OI buildup (CE side)             12 pts
  7.  Volume / OI turnover ratio        8 pts

  ── Live Market Depth (Order Flow) ────────────────────────────────────────
  8.  Bid/Ask imbalance                10 pts   ← NEW
      How many buyers vs sellers are queued right now.
      Imbalance > 0.65 = buyers dominating the order book.

  ── Structural / Contextual ───────────────────────────────────────────────
  9.  Moneyness (ATM ± 2 strikes)      10 pts
  10. Time-of-day window                5 pts

  ── LLM (Claude Haiku, multi-timeframe) ────────────────────────────────────
  11. LLM confidence blend              5 pts   ← NEW
      Uses the same LLMAnalyzer from signal_engine.
      Receives weekly/daily/hourly candles + indicators.
      Returns BUY/HOLD + reasoning. Gracefully disabled if no API key.

Final score 0–100; mapped to a letter grade + win-probability range.
LLM adds at most 5 pts but its reasoning is always shown in the card.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from kite_service import KiteService
from llm_analyzer import LLMAnalyzer

try:
    import pandas as pd
    import numpy as np
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    logging.warning("pandas/ta/numpy not available — WinProbabilityEngine will not function")

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── Weight constants ──────────────────────────────────────────────────────────
W_RSI         = 12   # RSI oversold + reversal slope
W_MACD        = 18   # MACD crossover & histogram momentum
W_BB          = 8    # Bollinger Band — price near/below lower band
W_EMA         = 8    # EMA9 > EMA21 → bullish structure
W_ATR         = 4    # ATR in the "sweet spot" (not too low, not too high)
W_OI          = 12   # Rising open interest on CE side (confirms call demand)
W_VOLUME      = 8    # Volume / OI turnover ratio
W_ORDER_FLOW  = 10   # Bid/Ask market depth imbalance (live order book)
W_MONEYNESS   = 10   # ATM ± 2 strikes preferred
W_TIME        = 5    # Intraday time-window bonus
W_LLM         = 5    # LLM multi-timeframe confidence blend

TOTAL_WEIGHT  = (W_RSI + W_MACD + W_BB + W_EMA + W_ATR +
                 W_OI + W_VOLUME + W_ORDER_FLOW +
                 W_MONEYNESS + W_TIME + W_LLM)
assert TOTAL_WEIGHT == 100, f"Weights must sum to 100, got {TOTAL_WEIGHT}"

# ── Grade thresholds ──────────────────────────────────────────────────────────
GRADE_MAP = [
    (80, "A+", "Very High",   ">80%"),
    (65, "A",  "High",        "65–80%"),
    (50, "B",  "Moderate",    "50–65%"),
    (35, "C",  "Low",         "35–50%"),
    (0,  "D",  "Very Low",    "<35%"),
]

# ── Time windows (IST, 24-h) that historically show better intraday momentum ──
# Primary: 09:45–11:30  (post-open momentum establishes)
# Secondary: 13:00–13:20 (last-hour momentum before 13:25 force-exit)
GOOD_WINDOWS = [
    (9, 45, 11, 30),
    (13, 0, 13, 20),
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _grade(score: float) -> Tuple[str, str, str]:
    """Return (letter_grade, label, win_prob_range) for a score 0–100."""
    for threshold, grade, label, prob in GRADE_MAP:
        if score >= threshold:
            return grade, label, prob
    return "D", "Very Low", "<35%"


def _time_bonus() -> Tuple[float, str]:
    """Return (pts, reason) based on current IST time."""
    now = datetime.now(IST)
    h, m = now.hour, now.minute
    for (sh, sm, eh, em) in GOOD_WINDOWS:
        if (h, m) >= (sh, sm) and (h, m) <= (eh, em):
            return W_TIME, f"In prime trading window {sh:02d}:{sm:02d}–{eh:02d}:{em:02d} IST"
    return 0.0, f"Outside prime windows (current IST {h:02d}:{m:02d})"


# ─────────────────────────────────────────────────────────────────────────────
# Main engine class
# ─────────────────────────────────────────────────────────────────────────────

class WinProbabilityEngine:
    """
    Scores a list of NFO call options by win probability for an intraday BUY.
    Designed to be stateless: instantiate once per request.
    """

    _MIN_CANDLES = 30  # need ≥30 hourly candles for reliable TA

    # Shared LLM analyzer — one instance per process
    _llm = LLMAnalyzer()

    def __init__(self, kite: KiteService):
        self.kite = kite

    # ── Data fetchers ─────────────────────────────────────────────────────────

    def _fetch_ohlcv(self, token: int, days: int = 60) -> Optional[object]:
        """
        Fetch hourly OHLCV DataFrame for `token` covering the last `days` days.
        Returns None when data is insufficient.
        """
        if not TA_AVAILABLE:
            logger.warning("OHLCV: pandas/ta not available")
            return None
        to_dt   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        from_dt = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            records = self.kite.get_historical_data(token, from_dt, to_dt, "60minute")
        except Exception as exc:
            logger.warning(f"OHLCV fetch failed for token {token}: {exc}")
            return None
        candle_count = len(records) if records else 0
        logger.info(f"OHLCV token={token} got {candle_count} candles (need ≥{self._MIN_CANDLES})")
        if not records or candle_count < self._MIN_CANDLES:
            logger.warning(f"OHLCV token={token}: insufficient candles ({candle_count} < {self._MIN_CANDLES})")
            return None
        df = pd.DataFrame(records)
        df.rename(columns={"date": "datetime"}, inplace=True)
        return df

    def _fetch_quote(self, tradingsymbol: str) -> Dict:
        """
        Fetch full quote (including OI, volume, depth) for a single NFO instrument.
        Returns empty dict on failure.
        """
        key = f"NFO:{tradingsymbol}"
        try:
            # self.kite is KiteService; self.kite.kite is the KiteConnect SDK object
            q = self.kite.kite.quote([key])
            result = q.get(key, {})
            if not result:
                logger.warning(f"Quote for {tradingsymbol}: empty response from Kite")
            else:
                logger.info(
                    f"Quote {tradingsymbol}: lp={result.get('last_price')}, "
                    f"oi={result.get('oi')}, vol={result.get('volume')}"
                )
            return result
        except Exception as exc:
            logger.warning(f"Quote fetch failed for {tradingsymbol}: {exc}")
            return {}

    def _fetch_multi_timeframe(self, underlying_token: int) -> Dict:
        """
        Fetch daily (90 days → last 30 rows) and weekly (200 days → last 20 rows)
        candles for the underlying index.  Used by the LLM scorer.
        Returns {"daily": [...], "weekly": [...]} — empty lists on failure.
        """
        result = {"daily": [], "weekly": []}
        if not TA_AVAILABLE:
            return result
        now     = datetime.now()
        to_dt   = now.strftime("%Y-%m-%d %H:%M:%S")

        try:
            from_daily = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
            daily = self.kite.get_historical_data(underlying_token, from_daily, to_dt, "day")
            if daily:
                result["daily"] = daily[-30:]
        except Exception:
            pass

        try:
            from_weekly = (now - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
            weekly = self.kite.get_historical_data(underlying_token, from_weekly, to_dt, "week")
            if weekly:
                result["weekly"] = weekly[-20:]
        except Exception:
            pass

        return result

    def _fetch_spot_price(self, underlying: str) -> Optional[float]:
        """
        Fetch the current spot (index) price for the underlying.
        Tries multiple Kite symbol formats — Kite requires exact names like "NIFTY 50".
        """
        # Multiple candidates per underlying — Kite is strict about exact symbol names
        SPOT_CANDIDATES = {
            "NIFTY":       ["NSE:NIFTY 50",    "NSE:NIFTY50",   "NSE:NIFTY"],
            "BANKNIFTY":   ["NSE:NIFTY BANK",  "NSE:BANKNIFTY"],
            "NIFTYNXT50":  ["NSE:NIFTY NEXT 50", "NSE:NIFTYNXT50"],
            "NIFTYMIDCAP": ["NSE:NIFTY MIDCAP 150", "NSE:NIFTYMIDCAP"],
            "MIDCPNIFTY":  ["NSE:NIFTY MIDCAP 150", "NSE:MIDCPNIFTY"],
            "NIFTYMNXT50": ["NSE:NIFTY NEXT 50"],
        }
        candidates = SPOT_CANDIDATES.get(underlying.upper(), [f"NSE:{underlying}"])
        try:
            ltp_map = self.kite.get_ltp(candidates)
            price = next((ltp_map[k] for k in candidates if ltp_map.get(k, 0) > 0), None)
            logger.info(f"Spot price fetch for {underlying}: {price} (tried {candidates})")
            return price
        except Exception as exc:
            logger.warning(f"Spot price fetch failed for {underlying}: {exc}")
            return None

    # ── Individual factor scorers ─────────────────────────────────────────────

    def _score_rsi(self, df: object) -> Tuple[float, str]:
        """RSI: full score when oversold + turning up; partial for near-oversold."""
        closes = df["close"]
        rsi = ta.momentum.RSIIndicator(closes, window=14).rsi()
        r, r_prev = float(rsi.iloc[-1]), float(rsi.iloc[-2])

        if r <= 30:
            pts = W_RSI if r > r_prev else W_RSI * 0.7
            return pts, f"RSI oversold {r:.1f} {'↑ recovering' if r > r_prev else '(still falling)'}"
        elif r <= 35 and r > r_prev:
            return W_RSI * 0.5, f"RSI near-oversold {r:.1f} ↑ recovering"
        elif r <= 45 and r > r_prev:
            return W_RSI * 0.25, f"RSI {r:.1f} ↑ rising momentum"
        return 0.0, f"RSI {r:.1f} — no oversold signal"

    def _score_macd(self, df: object) -> Tuple[float, str]:
        """MACD: crossover (histogram flips +) scores highest; ongoing momentum scores partial."""
        closes = df["close"]
        macd_obj  = ta.trend.MACD(closes, window_fast=12, window_slow=26, window_sign=9)
        hist      = macd_obj.macd_diff()
        h, h_prev = float(hist.iloc[-1]), float(hist.iloc[-2])

        if h > 0 and h_prev <= 0:
            # Fresh bullish crossover this candle — strongest signal
            return W_MACD, f"MACD fresh bullish crossover ({h_prev:.4f} → {h:.4f})"
        elif h > 0 and h > h_prev:
            # Already positive and accelerating
            pts = W_MACD * 0.65
            return pts, f"MACD histogram rising {h:.4f} (momentum building)"
        elif h > 0:
            # Positive but slowing
            return W_MACD * 0.35, f"MACD histogram positive {h:.4f} (decelerating)"
        elif h > h_prev and h_prev < 0:
            # Still negative but turning — early signal
            return W_MACD * 0.20, f"MACD histogram rising from negative ({h:.4f})"
        return 0.0, f"MACD histogram negative and falling ({h:.4f})"

    def _score_bollinger(self, df: object) -> Tuple[float, str]:
        """Bollinger Bands: price at/below lower band = oversold, high win prob for bounce."""
        closes = df["close"]
        bb     = ta.volatility.BollingerBands(closes, window=20, window_dev=2)
        lower  = float(bb.bollinger_lband().iloc[-1])
        mid    = float(bb.bollinger_mavg().iloc[-1])
        upper  = float(bb.bollinger_hband().iloc[-1])
        price  = float(closes.iloc[-1])

        band_width = upper - lower
        if band_width == 0:
            return 0.0, "BB: insufficient data"

        # Where is price within the band? 0 = lower, 1 = upper
        position = (price - lower) / band_width

        if position <= 0.10:
            return W_BB, f"Price at/below BB lower band ({price:.2f} ≤ {lower:.2f})"
        elif position <= 0.25:
            return W_BB * 0.75, f"Price near BB lower band (position {position:.0%})"
        elif position <= 0.50:
            return W_BB * 0.40, f"Price in lower half of BB (position {position:.0%})"
        elif position >= 0.90:
            # Near upper band — overbought, not ideal for CE entry
            return 0.0, f"Price near BB upper band (overbought, position {position:.0%})"
        return W_BB * 0.10, f"Price mid-BB (position {position:.0%})"

    def _score_ema_trend(self, df: object) -> Tuple[float, str]:
        """EMA 9 > EMA 21: bullish short-term structure."""
        closes = df["close"]
        ema9  = ta.trend.EMAIndicator(closes, window=9).ema_indicator()
        ema21 = ta.trend.EMAIndicator(closes, window=21).ema_indicator()
        e9, e21       = float(ema9.iloc[-1]),  float(ema21.iloc[-1])
        e9_p, e21_p   = float(ema9.iloc[-2]),  float(ema21.iloc[-2])

        if e9 > e21:
            # Check if it just crossed (golden cross this candle)
            if e9_p <= e21_p:
                return W_EMA, f"EMA9 × EMA21 golden cross! ({e9:.2f} > {e21:.2f})"
            gap_pct = (e9 - e21) / e21 * 100
            return W_EMA * 0.75, f"EMA9 > EMA21 bullish ({gap_pct:.2f}% gap)"
        elif e9 < e21 and e9 > e9_p:
            # Below but EMA9 is closing the gap
            return W_EMA * 0.25, f"EMA9 rising toward EMA21 (gap closing)"
        return 0.0, f"EMA9 < EMA21 bearish structure ({e9:.2f} < {e21:.2f})"

    def _score_atr_volatility(self, df: object) -> Tuple[float, str]:
        """
        ATR as % of price.  Sweet spot: 0.5–3.0% (enough movement without chaos).
        Too low → stagnant option, no profit opportunity.
        Too high → whipsaw risk for intraday.
        """
        atr_series = ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        ).average_true_range()
        atr   = float(atr_series.iloc[-1])
        price = float(df["close"].iloc[-1])
        if price == 0:
            return 0.0, "ATR: price is zero"

        atr_pct = (atr / price) * 100

        if 0.8 <= atr_pct <= 2.5:
            return W_ATR, f"ATR in ideal range {atr_pct:.2f}% (good intraday movement)"
        elif 0.5 <= atr_pct < 0.8 or 2.5 < atr_pct <= 3.5:
            return W_ATR * 0.5, f"ATR acceptable {atr_pct:.2f}%"
        elif atr_pct < 0.5:
            return 0.0, f"ATR too low {atr_pct:.2f}% — option stagnant"
        return 0.0, f"ATR too high {atr_pct:.2f}% — excessive whipsaw risk"

    def _score_oi(self, quote: Dict, tradingsymbol: str) -> Tuple[float, str]:
        """
        Open Interest buildup: rising OI + rising price = bullish confirmation.
        Kite quote returns: oi (current OI), oi_day_high, oi_day_low.
        """
        oi           = quote.get("oi", 0) or 0
        oi_day_low   = quote.get("oi_day_low", 0) or 0
        volume       = quote.get("volume", 0) or 0
        last_price   = quote.get("last_price", 0) or 0

        if oi == 0:
            return 0.0, "OI data unavailable from Kite"

        # OI buildup: current OI significantly above day low means buyers accumulating
        if oi_day_low > 0:
            oi_increase_pct = (oi - oi_day_low) / oi_day_low * 100
            if oi_increase_pct >= 10 and last_price > 0:
                return W_OI, f"OI buildup +{oi_increase_pct:.1f}% from day low ({oi:,} contracts)"
            elif oi_increase_pct >= 5:
                return W_OI * 0.65, f"Moderate OI buildup +{oi_increase_pct:.1f}%"
            elif oi_increase_pct >= 0:
                return W_OI * 0.30, f"Stable OI ({oi_increase_pct:.1f}% from low)"
            else:
                # OI is falling — long unwinding
                return 0.0, f"OI falling {oi_increase_pct:.1f}% from high (long unwinding)"

        # Fallback: just use absolute OI as proxy for liquidity
        if oi >= 1_000_000:
            return W_OI * 0.5, f"High absolute OI ({oi:,}) — good liquidity"
        elif oi >= 100_000:
            return W_OI * 0.25, f"Moderate OI ({oi:,})"
        return 0.0, f"Low OI ({oi:,}) — illiquid option"

    def _score_volume(self, quote: Dict) -> Tuple[float, str]:
        """
        Option volume activity score.

        We cannot compare option volume to index OHLCV volume — they are on
        completely different scales (index volume = crores of rupees traded;
        option volume = number of lots).  Instead we use two option-specific
        signals available from the live quote:

        1. Volume-to-OI ratio (turnover ratio):
           High volume relative to OI means active intraday participation,
           not just stale open positions.
             ≥ 0.20  → heavy activity (≥20% of OI traded today)
             ≥ 0.10  → moderate activity
             ≥ 0.03  → light activity

        2. Absolute volume floor (liquidity gate):
           Very low absolute volume = illiquid option, hard to exit.
             ≥ 10,000 lots traded today → liquid enough
        """
        volume = quote.get("volume", 0) or 0
        oi     = quote.get("oi", 0) or 0

        if volume == 0:
            return 0.0, "Volume data unavailable from Kite quote"

        # Liquidity gate — absolute minimum
        if volume < 500:
            return 0.0, f"Very low option volume ({volume:,} lots) — illiquid"

        if oi > 0:
            turnover_ratio = volume / oi
            if turnover_ratio >= 0.20:
                return W_VOLUME, f"High option activity: volume/OI = {turnover_ratio:.0%} ({volume:,} lots)"
            elif turnover_ratio >= 0.10:
                return W_VOLUME * 0.65, f"Moderate option activity: volume/OI = {turnover_ratio:.0%}"
            elif turnover_ratio >= 0.03:
                return W_VOLUME * 0.30, f"Light option activity: volume/OI = {turnover_ratio:.0%} ({volume:,} lots)"
            return 0.0, f"Very low turnover ratio {turnover_ratio:.1%} — stale OI, no fresh buying"

        # OI unavailable — fall back to absolute volume bands
        if volume >= 50_000:
            return W_VOLUME * 0.75, f"High absolute volume ({volume:,} lots)"
        elif volume >= 10_000:
            return W_VOLUME * 0.40, f"Moderate absolute volume ({volume:,} lots)"
        return W_VOLUME * 0.15, f"Low absolute volume ({volume:,} lots) — check liquidity"

    def _score_moneyness(
        self, strike: float, spot_price: Optional[float], lot_size: int
    ) -> Tuple[float, str]:
        """
        ATM ± 2 strikes preferred for balanced premium + delta.
        Deep OTM = cheap but low delta; Deep ITM = high premium, less leveraged.

        For NIFTY, typical strike gap = 50; for BANKNIFTY = 100.
        We compute moneyness as (strike - spot) / spot × 100.
        """
        if not spot_price or spot_price == 0 or not strike:
            return W_MONEYNESS * 0.3, "Spot price unavailable — moneyness skipped"

        moneyness_pct = (strike - spot_price) / spot_price * 100  # + = OTM, - = ITM

        if -0.5 <= moneyness_pct <= 1.0:
            return W_MONEYNESS, f"ATM strike {strike:.0f} (spot {spot_price:.0f}, {moneyness_pct:+.2f}%)"
        elif -1.5 <= moneyness_pct <= 2.5:
            return W_MONEYNESS * 0.75, f"Near-ATM strike {strike:.0f} ({moneyness_pct:+.2f}% OTM)"
        elif -3.0 <= moneyness_pct <= 4.0:
            return W_MONEYNESS * 0.40, f"Slightly OTM/ITM {strike:.0f} ({moneyness_pct:+.2f}%)"
        elif moneyness_pct > 4.0:
            return 0.0, f"Deep OTM {strike:.0f} ({moneyness_pct:+.2f}%) — low delta"
        else:
            return W_MONEYNESS * 0.20, f"ITM strike {strike:.0f} ({moneyness_pct:+.2f}%)"

    def _score_order_flow(self, depth: Dict) -> Tuple[float, str]:
        """
        Order book imbalance — compares total queued BUY quantity vs SELL quantity
        across all 5 bid/ask levels from Kite's market depth API.

        This is the closest we can get to real-time order flow without a tick-by-tick
        WebSocket feed.  A heavily imbalanced book tells you which side of the market
        has more committed orders RIGHT NOW, before the next candle closes.

        Scoring:
          imbalance ≥ 0.70  → strongly buyer-dominated            full score
          imbalance ≥ 0.60  → moderately buyer-dominated          75%
          imbalance ≥ 0.50  → slight buyer edge                   40%
          imbalance ≥ 0.40  → balanced / slight seller edge       10%
          imbalance <  0.40 → sellers dominating                  0 pts

        Also penalises if spread > 1% of mid-price (illiquid option — hard to exit).
        """
        if not depth:
            return W_ORDER_FLOW * 0.3, "Order book data unavailable — assuming neutral"

        imbalance  = depth.get("imbalance", 0.5)
        spread_pct = depth.get("spread_pct", 0)
        buy_qty    = depth.get("total_buy_qty", 0)
        sell_qty   = depth.get("total_sell_qty", 0)

        # Illiquidity penalty — wide spread means hard to exit profitably
        liquidity_factor = 1.0
        if spread_pct > 1.0:
            liquidity_factor = 0.5
            spread_note = f", wide spread {spread_pct:.2f}% (illiquid)"
        elif spread_pct > 0.5:
            liquidity_factor = 0.8
            spread_note = f", spread {spread_pct:.2f}%"
        else:
            spread_note = f", tight spread {spread_pct:.2f}%"

        if buy_qty == 0 and sell_qty == 0:
            return W_ORDER_FLOW * 0.3, "Order book empty — market may be closed"

        if imbalance >= 0.70:
            pts = W_ORDER_FLOW * liquidity_factor
            return pts, f"Strong buyer dominance: {buy_qty:,} bid vs {sell_qty:,} ask ({imbalance:.0%} buy){spread_note}"
        elif imbalance >= 0.60:
            pts = W_ORDER_FLOW * 0.75 * liquidity_factor
            return pts, f"Moderate buyer edge: {imbalance:.0%} buy side{spread_note}"
        elif imbalance >= 0.50:
            pts = W_ORDER_FLOW * 0.40 * liquidity_factor
            return pts, f"Slight buyer edge: {imbalance:.0%} buy side{spread_note}"
        elif imbalance >= 0.40:
            pts = W_ORDER_FLOW * 0.10
            return pts, f"Balanced order book: {imbalance:.0%} buy side{spread_note}"
        else:
            return 0.0, f"Seller dominated: only {imbalance:.0%} buy side ({buy_qty:,} bid vs {sell_qty:,} ask){spread_note}"

    def _score_llm(
        self,
        tradingsymbol: str,
        df: Optional[object],
        indicators: Optional[Dict],
        spot_price: Optional[float],
        underlying_token: int,
    ) -> Tuple[float, str, List[str]]:
        """
        Run LLM (Claude Haiku) multi-timeframe analysis on the underlying index.
        Returns (pts, summary_reason, full_reasoning_list).

        The LLM gets:
          - Weekly candles (last 20 weeks)  → dominant trend
          - Daily candles  (last 30 days)   → swing highs/lows, key levels
          - Hourly candles (last 20 bars)   → entry trigger
          - RSI + MACD indicators

        Maps LLM confidence to W_LLM points:
          LLM BUY  ≥ 70  → full W_LLM pts
          LLM BUY  ≥ 50  → 60% of W_LLM
          LLM BUY  <  50 → 0 pts (LLM not confident enough to contribute)
          LLM HOLD       → 0 pts
          LLM unavailable → 0 pts, neutral note added
        """
        if not self._llm.available:
            return 0.0, "LLM unavailable (ANTHROPIC_API_KEY not set)", []

        if df is None or indicators is None:
            return 0.0, "LLM skipped — no OHLCV data", []

        try:
            mtf      = self._fetch_multi_timeframe(underlying_token)
            ohlcv_rows = df.to_dict(orient="records") if df is not None else []

            result = self._llm.analyze(
                tradingsymbol=tradingsymbol,
                ohlcv_rows=ohlcv_rows,
                indicators=indicators,
                spot_price=spot_price or 0.0,
                daily_rows=mtf["daily"],
                weekly_rows=mtf["weekly"],
                support_resistance={},
            )

            direction  = result["direction"]
            confidence = result["confidence"]
            reasoning  = result["reasoning"]

            if direction == "BUY" and confidence >= 70:
                pts    = W_LLM
                reason = f"LLM BUY {confidence}% — strong multi-TF alignment"
            elif direction == "BUY" and confidence >= 50:
                pts    = W_LLM * 0.6
                reason = f"LLM BUY {confidence}% — moderate multi-TF alignment"
            else:
                pts    = 0.0
                reason = f"LLM {direction} {confidence}% — not contributing to score"

            return pts, reason, reasoning

        except Exception as exc:
            logger.warning(f"LLM scoring failed for {tradingsymbol}: {exc}")
            return 0.0, f"LLM error: {exc}", []

    # ── Master scorer ─────────────────────────────────────────────────────────

    def score_instrument(
        self,
        tradingsymbol: str,
        instrument_token: int,
        strike: float,
        expiry,
        lot_size: int,
        underlying_token: int,
        spot_price: Optional[float],
    ) -> Dict:
        """
        Compute full win-probability score for one instrument.
        Returns a rich dict ready to be sent to the frontend.
        """
        factors   = {}
        total_pts = 0.0

        # ── 1. Fetch data ──────────────────────────────────────────────────────
        # ALWAYS use the underlying INDEX token for all TA (RSI, MACD, BB, EMA,
        # ATR).  Option contracts exist for only 7–30 days so their own OHLCV
        # never has enough candles for reliable indicators.  The index token
        # (e.g. 256265 for NIFTY 50) has years of history and is what all
        # technical analysis should be based on anyway — we're trading the
        # direction of the index, not the option premium itself.
        df = self._fetch_ohlcv(underlying_token, days=60)
        # No fallback to instrument_token — a short-dated option token will
        # never have 30+ hourly candles, so the fallback always returns None
        # and wastes an extra Kite API call.

        quote = self._fetch_quote(tradingsymbol)
        depth = self.kite.get_market_depth(tradingsymbol)

        # ── 2. Compute indicators once (reused by TA scorers + LLM) ──────────
        indicators = None
        if df is not None and TA_AVAILABLE:
            try:
                closes = df["close"]
                rsi_s  = ta.momentum.RSIIndicator(closes, window=14).rsi()
                macd_o = ta.trend.MACD(closes, window_fast=12, window_slow=26, window_sign=9)
                indicators = {
                    "rsi":                   float(rsi_s.iloc[-1]),
                    "rsi_prev":              float(rsi_s.iloc[-2]),
                    "macd_histogram":        float(macd_o.macd_diff().iloc[-1]),
                    "macd_histogram_prev":   float(macd_o.macd_diff().iloc[-2]),
                    "macd_line":             float(macd_o.macd().iloc[-1]),
                    "signal_line":           float(macd_o.macd_signal().iloc[-1]),
                    "current_price":         float(closes.iloc[-1]),
                    "price_trend_pct":       float(
                        (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6] * 100
                        if len(closes) >= 6 else 0
                    ),
                }
            except Exception:
                indicators = None

        # ── 3. Score each factor ──────────────────────────────────────────────

        # TA factors — require index OHLCV
        if df is not None:
            pts, reason = self._score_rsi(df)
            factors["RSI"]        = {"pts": round(pts, 1), "max": W_RSI,   "reason": reason}
            total_pts += pts

            pts, reason = self._score_macd(df)
            factors["MACD"]       = {"pts": round(pts, 1), "max": W_MACD,  "reason": reason}
            total_pts += pts

            pts, reason = self._score_bollinger(df)
            factors["Bollinger"]  = {"pts": round(pts, 1), "max": W_BB,    "reason": reason}
            total_pts += pts

            pts, reason = self._score_ema_trend(df)
            factors["EMA Trend"]  = {"pts": round(pts, 1), "max": W_EMA,   "reason": reason}
            total_pts += pts

            pts, reason = self._score_atr_volatility(df)
            factors["ATR"]        = {"pts": round(pts, 1), "max": W_ATR,   "reason": reason}
            total_pts += pts
        else:
            for name, w in [("RSI", W_RSI), ("MACD", W_MACD), ("Bollinger", W_BB),
                            ("EMA Trend", W_EMA), ("ATR", W_ATR)]:
                factors[name] = {"pts": 0, "max": w, "reason": "Index OHLCV unavailable"}

        # Volume — option quote only
        pts, reason = self._score_volume(quote)
        factors["Volume"] = {"pts": round(pts, 1), "max": W_VOLUME, "reason": reason}
        total_pts += pts

        # OI buildup — option quote only
        pts, reason = self._score_oi(quote, tradingsymbol)
        factors["OI Buildup"] = {"pts": round(pts, 1), "max": W_OI, "reason": reason}
        total_pts += pts

        # Order Flow — live bid/ask market depth (NEW)
        pts, reason = self._score_order_flow(depth)
        factors["Order Flow"] = {"pts": round(pts, 1), "max": W_ORDER_FLOW, "reason": reason}
        total_pts += pts

        # Moneyness
        pts, reason = self._score_moneyness(strike, spot_price, lot_size)
        factors["Moneyness"] = {"pts": round(pts, 1), "max": W_MONEYNESS, "reason": reason}
        total_pts += pts

        # Time window
        pts, reason = _time_bonus()
        factors["Time Window"] = {"pts": round(pts, 1), "max": W_TIME, "reason": reason}
        total_pts += pts

        # LLM multi-timeframe (NEW) — runs last so all rule scores are complete
        pts, llm_reason, llm_reasoning = self._score_llm(
            tradingsymbol=tradingsymbol,
            df=df,
            indicators=indicators,
            spot_price=spot_price,
            underlying_token=underlying_token,
        )
        factors["LLM Analysis"] = {"pts": round(pts, 1), "max": W_LLM, "reason": llm_reason}
        total_pts += pts

        # ── 4. Normalise & grade ──────────────────────────────────────────────
        score       = round(min(100.0, max(0.0, total_pts)), 1)
        grade, label, win_prob = _grade(score)

        # ── 5. Fetch live LTP for the option ─────────────────────────────────
        last_price = (
            quote.get("last_price")
            or depth.get("best_bid")
            or self.kite.get_ltp([f"NFO:{tradingsymbol}"]).get(f"NFO:{tradingsymbol}")
        )

        # ── 6. Compute suggested entry / SL / target ─────────────────────────
        sl_pct  = 3.0
        tgt_pct = 8.0
        entry   = round(last_price, 2) if last_price else None
        sl      = round(last_price * (1 - sl_pct / 100), 2) if last_price else None
        target  = round(last_price * (1 + tgt_pct / 100), 2) if last_price else None

        # ── 7. Top 3 reasons (sorted by pts desc) ────────────────────────────
        top_reasons = [
            f"{name}: {v['reason']}"
            for name, v in sorted(factors.items(), key=lambda x: x[1]["pts"], reverse=True)
        ][:3]

        return {
            "rank":              None,
            "instrument":        tradingsymbol,
            "strike":            strike,
            "expiry":            str(expiry),
            "lot_size":          lot_size,
            "last_price":        entry,
            "score":             score,
            "grade":             grade,
            "win_probability":   win_prob,
            "win_label":         label,
            "suggested_entry":   entry,
            "suggested_sl":      sl,
            "suggested_target":  target,
            "top_reasons":       top_reasons,
            "factors":           factors,
            "llm_reasoning":     llm_reasoning,   # full LLM reasoning list
            "order_flow":        {                 # live order book snapshot
                "imbalance":       depth.get("imbalance"),
                "total_buy_qty":   depth.get("total_buy_qty"),
                "total_sell_qty":  depth.get("total_sell_qty"),
                "best_bid":        depth.get("best_bid"),
                "best_ask":        depth.get("best_ask"),
                "spread_pct":      depth.get("spread_pct"),
            } if depth else None,
            "moneyness_pct":     round((strike - spot_price) / spot_price * 100, 2) if spot_price else None,
            "spot_price":        round(spot_price, 2) if spot_price else None,
            "data_available":    df is not None,
            "llm_available":     self._llm.available,
        }

    # ── Public API ─────────────────────────────────────────────────────────────

    def rank_instruments(
        self,
        underlying: str,
        underlying_token: int,
        instruments: List[Dict],
        max_results: int = 10,
        spot_price: Optional[float] = None,
    ) -> Dict:
        """
        Score and rank all given instruments by win probability.

        Args:
            underlying:       e.g. "NIFTY"
            underlying_token: Kite instrument token for the index (for OHLCV)
            instruments:      list of instrument dicts from KiteService.get_option_instruments()
            max_results:      return top-N instruments
            spot_price:       optional pre-fetched spot price (avoids extra LTP call)

        Returns:
            {
                "underlying":     str,
                "spot_price":     float | None,
                "ranked":         list[scored_instrument],
                "scanned":        int,
                "scan_time_ms":   int,
                "generated_at":   str (ISO timestamp IST),
            }
        """
        import time
        t0 = time.monotonic()

        if spot_price is None:
            spot_price = self._fetch_spot_price(underlying)
        logger.info(f"Spot price for {underlying}: {spot_price}")

        scored = []
        for inst in instruments:
            symbol = inst.get("tradingsymbol")
            token  = inst.get("instrument_token")
            strike = inst.get("strike", 0)
            expiry = inst.get("expiry")
            lot    = inst.get("lot_size", 1)

            if not symbol or not token:
                continue

            try:
                result = self.score_instrument(
                    tradingsymbol=symbol,
                    instrument_token=token,
                    strike=strike,
                    expiry=expiry,
                    lot_size=lot,
                    underlying_token=underlying_token,
                    spot_price=spot_price,
                )
                scored.append(result)
            except Exception as exc:
                logger.warning(f"Scoring failed for {symbol}: {exc}")
                continue

        # Sort descending by score
        scored.sort(key=lambda x: x["score"], reverse=True)

        # Assign ranks
        for i, item in enumerate(scored, start=1):
            item["rank"] = i

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        return {
            "underlying":    underlying,
            "spot_price":    round(spot_price, 2) if spot_price else None,
            "ranked":        scored[:max_results],
            "scanned":       len(instruments),
            "scan_time_ms":  elapsed_ms,
            "generated_at":  datetime.now(IST).isoformat(),
        }
