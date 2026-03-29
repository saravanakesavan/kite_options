"""
Win Probability Engine  (v2 — Index-First Architecture)
=========================================================
Ranks NFO call-option instruments by their probability of generating
an intraday profit.

── KEY CHANGES FROM v1 ───────────────────────────────────────────────────────

  1. INDEX IS ALWAYS PRIMARY
     RSI, MACD, EMA, ATR now run exclusively on underlying index candles
     (NIFTY/BANKNIFTY hourly).  Option-price candles are unreliable for
     momentum indicators due to premium decay, time-value, and thin liquidity.

  2. BOLLINGER BANDS REMOVED
     BB on option price = noise (OTM options sit near lower band by design).
     Weight redistributed to EMA (stronger trend signal).

  3. ATR NOW ON INDEX
     ATR measures volatility of the underlying, not the option.
     Sweet spot: 0.3–1.2% per 60-min candle on NIFTY → good intraday range.

  4. PIVOT / S1 PROXIMITY ADDED  (Group D)
     S1 support bounce is one of the highest-probability CE entry setups.
     Daily pivot levels (PP, R1, S1) computed from yesterday's index candle.

  5. PCR (PUT-CALL RATIO) + INDIA VIX  (Group E — NEW)
     PCR ≥ 1.2  → over-hedged market, contrarian CE buy signal
     VIX 10–15  → ideal premium buying environment
     Hard veto if VIX > 18 (expensive options, theta kills intraday trades)
     Hard veto if PCR < 0.7 (crowd over-bullish, fade the calls)

  6. PRE-FETCH SHARED DATA
     rank_instruments() fetches index_df, daily_rows, VIX, PCR ONCE for all
     instruments.  No redundant Kite API calls per instrument.

── SCORING ARCHITECTURE ──────────────────────────────────────────────────────

  Group A — Momentum (RSI + MACD)                      30 pts
    RSI raw max = 12,  MACD raw max = 18
    Runs on INDEX 60-min candles.

  Group B — Trend Structure (EMA + Index ATR)           15 pts
    EMA raw max = 10,  ATR raw max = 5
    Runs on INDEX 60-min candles.  BB removed.

  Group C — Option Activity (OI + Volume)               15 pts
    OI raw max = 9,  Volume raw max = 6
    From option quote only (no index data needed).

  Group D — Live Market  (Order Flow + Moneyness + Pivot + Time)  25 pts
    Order Flow = 10, Moneyness = 8, Pivot Proximity = 5, Time = 2

  Group E — Market Sentiment  (PCR + VIX)              15 pts
    PCR raw max = 10,  VIX raw max = 5
    Pre-fetched once, shared across all instruments.

  LLM  (independent tiebreaker)                         5 pts

  GROUP_CAPS sum = 30+15+15+25+15+5 = 105 raw, but groups cap at exactly 100.

── HARD VETO GATES ───────────────────────────────────────────────────────────

  VETO 1 — Bear Trap Guard        (sellers dominate + OI falling)
  VETO 2 — Deep OTM Lock          (strike > 3.5% OTM)
  VETO 3 — Trend-Momentum Divergence (MACD+EMA both bearish)
  VETO 4 — Elevated VIX           (VIX > 18 → options too expensive)
  VETO 5 — Bearish PCR            (PCR < 0.7 → crowd over-bullish, fade)

── FACTOR WEIGHTS ────────────────────────────────────────────────────────────

  RSI              12 pts  Group A  (index candles)
  MACD             18 pts  Group A  (index candles)
  EMA Trend        10 pts  Group B  (index candles, was 8, BB weight absorbed)
  Index ATR         5 pts  Group B  (index candles, replaces option ATR)
  OI Buildup        9 pts  Group C  (option quote)
  Volume            6 pts  Group C  (option quote)
  Order Flow       10 pts  Group D  (live market depth)
  Moneyness         8 pts  Group D
  Pivot Proximity   5 pts  Group D  (NEW — S1/PP level bounce)
  Time Window       2 pts  Group D  (reduced; time is weaker predictor alone)
  PCR              10 pts  Group E  (NEW — Put-Call Ratio)
  India VIX         5 pts  Group E  (NEW — India VIX filter)
  LLM Analysis      5 pts  Independent
  ─────────────────────────────────────────────────────────
  Total group raw = 105,  GROUP_CAPS = 100 (groups are capped)
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
# Group A — Momentum (always on INDEX candles)
W_RSI         = 12   # RSI oversold + reversal slope
W_MACD        = 18   # MACD crossover & histogram momentum

# Group B — Trend Structure (always on INDEX candles, BB removed)
W_EMA         = 10   # EMA9 > EMA21 bullish structure (up from 8; absorbed BB weight)
W_ATR         = 5    # ATR on INDEX in sweet-spot range (not too low, not too high)

# Group C — Option Activity
W_OI          = 9    # Rising OI on CE side (call demand)
W_VOLUME      = 6    # Volume / OI turnover ratio

# Group D — Live Market Signals
W_ORDER_FLOW  = 10   # Bid/Ask market depth imbalance
W_MONEYNESS   = 8    # ATM ± 2 strikes preferred
W_PIVOT       = 5    # NEW — daily S1/Pivot proximity bonus
W_TIME        = 2    # Intraday time-window bonus (reduced)

# Group E — Market Sentiment (NEW)
W_PCR         = 10   # Put-Call Ratio
W_VIX         = 5    # India VIX filter

# Independent
W_LLM         = 5    # LLM multi-timeframe confidence blend

# Group caps — each group is capped at its cap value
GROUP_CAPS = {
    "A-Momentum":       W_RSI + W_MACD,                              # 30
    "B-TrendStructure": W_EMA + W_ATR,                               # 15
    "C-OptionActivity": W_OI + W_VOLUME,                             # 15
    "D-LiveMarket":     W_ORDER_FLOW + W_MONEYNESS + W_PIVOT + W_TIME,  # 25
    "E-Sentiment":      W_PCR + W_VIX,                               # 15
    "LLM":              W_LLM,                                        # 5
}
# Sum of caps = 30+15+15+25+15+5 = 105 raw max, but groups are capped independently
# so the final score is naturally ≤ 100

# ── VIX / PCR thresholds ──────────────────────────────────────────────────────
VIX_IDEAL_LOW  = 10.0   # Below this → market too stagnant, low premium movement
VIX_IDEAL_HIGH = 15.0   # Ideal upper bound for CE buying
VIX_ELEVATED   = 18.0   # Above this → VETO 4 fires (options too expensive)
PCR_BULLISH    = 1.2    # PCR ≥ 1.2 → contrarian CE buy (over-hedged)
PCR_NEUTRAL    = 0.9    # PCR 0.9–1.2 → neutral / moderate
PCR_BEARISH    = 0.7    # PCR < 0.7 → crowd over-bullish → VETO 5 fires

# ── Grade thresholds ──────────────────────────────────────────────────────────
GRADE_MAP = [
    (80, "A+", "Very High",   ">80%"),
    (65, "A",  "High",        "65–80%"),
    (50, "B",  "Moderate",    "50–65%"),
    (35, "C",  "Low",         "35–50%"),
    (0,  "D",  "Very Low",    "<35%"),
]

# ── Time windows (IST, 24-h) that historically show better intraday momentum ──
GOOD_WINDOWS = [
    (9, 45, 11, 30),    # Post-open momentum
    (13, 0, 13, 20),    # Last-hour momentum window
]


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
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


def _compute_pivot_levels(daily_rows: List[Dict]) -> Dict:
    """
    Compute standard pivot points from the previous day's daily candle.

    Formula:
        PP = (H + L + C) / 3
        R1 = 2×PP − L
        R2 = PP + (H − L)
        S1 = 2×PP − H
        S2 = PP − (H − L)

    Returns dict with pivot_pp, pivot_r1, pivot_r2, pivot_s1, pivot_s2.
    Returns {} if insufficient data.
    """
    if not daily_rows or len(daily_rows) < 2:
        return {}
    try:
        # Use the second-to-last row as "yesterday"
        prev = daily_rows[-2]
        H, L, C = float(prev["high"]), float(prev["low"]), float(prev["close"])
        PP = (H + L + C) / 3
        R1 = 2 * PP - L
        R2 = PP + (H - L)
        S1 = 2 * PP - H
        S2 = PP - (H - L)
        return {
            "pivot_pp": round(PP, 2),
            "pivot_r1": round(R1, 2),
            "pivot_r2": round(R2, 2),
            "pivot_s1": round(S1, 2),
            "pivot_s2": round(S2, 2),
        }
    except Exception as exc:
        logger.warning(f"Pivot computation failed: {exc}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Main engine class
# ─────────────────────────────────────────────────────────────────────────────

class WinProbabilityEngine:
    """
    Scores a list of NFO call options by win probability for an intraday BUY.
    Designed to be stateless: instantiate once per request.

    Index candles are always the primary data source for momentum/trend indicators.
    Option data (quote, depth) is only used for OI/Volume/OrderFlow.
    """

    _MIN_CANDLES = 30  # need ≥30 hourly candles for reliable TA

    # Shared LLM analyzer — one instance per process
    _llm = LLMAnalyzer()

    def __init__(self, kite: KiteService):
        self.kite = kite

    # ── Data fetchers ─────────────────────────────────────────────────────────

    def _fetch_index_ohlcv(self, underlying_token: int, days: int = 60) -> Optional[object]:
        """
        Fetch hourly OHLCV DataFrame for the underlying INDEX token.
        Always fetches the index, never the option token.
        Returns None when data is insufficient.
        """
        if not TA_AVAILABLE:
            logger.warning("OHLCV: pandas/ta not available")
            return None
        to_dt   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        from_dt = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            records = self.kite.get_historical_data(underlying_token, from_dt, to_dt, "60minute")
        except Exception as exc:
            logger.warning(f"Index OHLCV fetch failed for token {underlying_token}: {exc}")
            return None
        candle_count = len(records) if records else 0
        logger.info(f"Index OHLCV token={underlying_token} got {candle_count} candles (need ≥{self._MIN_CANDLES})")
        if not records or candle_count < self._MIN_CANDLES:
            logger.warning(f"Index OHLCV token={underlying_token}: insufficient candles ({candle_count})")
            return None
        df = pd.DataFrame(records)
        df.rename(columns={"date": "datetime"}, inplace=True)
        return df

    def _fetch_daily_ohlcv(self, underlying_token: int, days: int = 90) -> List[Dict]:
        """
        Fetch daily candles for the underlying index.
        Used for pivot level computation and LLM multi-timeframe context.
        Returns list of raw dicts (not a DataFrame).
        """
        to_dt   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        from_dt = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            records = self.kite.get_historical_data(underlying_token, from_dt, to_dt, "day")
            return records or []
        except Exception as exc:
            logger.warning(f"Daily OHLCV fetch failed for token {underlying_token}: {exc}")
            return []

    def _fetch_quote(self, tradingsymbol: str) -> Dict:
        """
        Fetch full quote (including OI, volume, depth) for a single NFO instrument.
        Returns empty dict on failure.
        """
        key = f"NFO:{tradingsymbol}"
        try:
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

    def _fetch_vix(self) -> Optional[float]:
        """
        Fetch India VIX value.
        India VIX is listed on NSE as "INDIA VIX".
        Returns None if unavailable.
        """
        candidates = ["NSE:INDIA VIX", "NSE:INDIAVIX"]
        try:
            ltp_map = self.kite.get_ltp(candidates)
            for key in candidates:
                val = ltp_map.get(key, 0)
                if val and val > 0:
                    logger.info(f"India VIX fetched: {val:.2f}")
                    return float(val)
            logger.warning("India VIX: no valid value from Kite (market may be closed)")
            return None
        except Exception as exc:
            logger.warning(f"India VIX fetch failed: {exc}")
            return None

    def _fetch_pcr(self, underlying: str) -> Optional[float]:
        """
        Compute Put-Call Ratio for the given underlying.
        PCR = Total PE Open Interest / Total CE Open Interest for nearest expiry.

        Fetches NFO instruments, filters to the nearest expiry (by date),
        then sums CE and PE OI from live quotes in batches of 500.

        Returns PCR as a float, or None if data unavailable.
        """
        try:
            instruments = self.kite.kite.instruments("NFO")
            if not instruments:
                return None

            # Filter to this underlying and active options only
            ce_instruments = [
                inst for inst in instruments
                if inst.get("name") == underlying and inst.get("instrument_type") == "CE"
            ]
            pe_instruments = [
                inst for inst in instruments
                if inst.get("name") == underlying and inst.get("instrument_type") == "PE"
            ]

            if not ce_instruments or not pe_instruments:
                logger.warning(f"PCR: no CE/PE instruments found for {underlying}")
                return None

            # Find nearest expiry (sorted ascending)
            all_expiries = sorted(set(
                inst["expiry"] for inst in ce_instruments if inst.get("expiry")
            ))
            if not all_expiries:
                return None
            nearest_expiry = all_expiries[0]

            # Filter to nearest expiry only
            ce_near = [i for i in ce_instruments if i.get("expiry") == nearest_expiry]
            pe_near = [i for i in pe_instruments if i.get("expiry") == nearest_expiry]

            if not ce_near or not pe_near:
                return None

            def _fetch_batch_oi(inst_list: List[Dict]) -> float:
                """Sum OI for a list of instruments in batches of 500."""
                total_oi = 0.0
                symbols = [f"NFO:{i['tradingsymbol']}" for i in inst_list]
                # Kite limits quote() to 500 instruments
                for i in range(0, len(symbols), 500):
                    batch = symbols[i:i+500]
                    try:
                        quotes = self.kite.kite.quote(batch)
                        for q in quotes.values():
                            total_oi += q.get("oi", 0) or 0
                    except Exception as exc:
                        logger.warning(f"PCR batch OI fetch failed: {exc}")
                return total_oi

            ce_oi = _fetch_batch_oi(ce_near)
            pe_oi = _fetch_batch_oi(pe_near)

            if ce_oi <= 0:
                logger.warning(f"PCR: CE OI is zero for {underlying}")
                return None

            pcr = round(pe_oi / ce_oi, 3)
            logger.info(f"PCR for {underlying} (expiry {nearest_expiry}): {pcr:.3f} "
                        f"(PE OI={pe_oi:,.0f}, CE OI={ce_oi:,.0f})")
            return pcr

        except Exception as exc:
            logger.warning(f"PCR computation failed for {underlying}: {exc}")
            return None

    def _fetch_spot_price(self, underlying: str) -> Optional[float]:
        """Fetch the current spot (index) price for the underlying."""
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
            logger.info(f"Spot price for {underlying}: {price}")
            return price
        except Exception as exc:
            logger.warning(f"Spot price fetch failed for {underlying}: {exc}")
            return None

    def _fetch_multi_timeframe(self, underlying_token: int) -> Dict:
        """
        Fetch daily (last 30 rows) and weekly (last 20 rows) candles.
        Used by the LLM scorer for multi-timeframe context.
        """
        result = {"daily": [], "weekly": []}
        if not TA_AVAILABLE:
            return result
        now   = datetime.now()
        to_dt = now.strftime("%Y-%m-%d %H:%M:%S")
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

    # ── Individual factor scorers ─────────────────────────────────────────────

    def _score_rsi(self, df: object) -> Tuple[float, str]:
        """
        RSI on INDEX candles.
        Full score when oversold (≤30) and turning up; partial for near-oversold.
        """
        closes = df["close"]
        rsi = ta.momentum.RSIIndicator(closes, window=14).rsi()
        r, r_prev = float(rsi.iloc[-1]), float(rsi.iloc[-2])

        if r <= 30:
            pts = W_RSI if r > r_prev else W_RSI * 0.7
            return pts, f"Index RSI oversold {r:.1f} {'↑ recovering' if r > r_prev else '(still falling)'}"
        elif r <= 35 and r > r_prev:
            return W_RSI * 0.5, f"Index RSI near-oversold {r:.1f} ↑ recovering"
        elif r <= 45 and r > r_prev:
            return W_RSI * 0.25, f"Index RSI {r:.1f} ↑ rising momentum"
        return 0.0, f"Index RSI {r:.1f} — no oversold signal"

    def _score_macd(self, df: object) -> Tuple[float, str]:
        """
        MACD on INDEX candles.
        Fresh crossover (histogram flips +) scores highest.
        """
        closes = df["close"]
        macd_obj  = ta.trend.MACD(closes, window_fast=12, window_slow=26, window_sign=9)
        hist      = macd_obj.macd_diff()
        h, h_prev = float(hist.iloc[-1]), float(hist.iloc[-2])

        if h > 0 and h_prev <= 0:
            return W_MACD, f"Index MACD fresh bullish crossover ({h_prev:.4f} → {h:.4f})"
        elif h > 0 and h > h_prev:
            return W_MACD * 0.65, f"Index MACD histogram rising {h:.4f} (momentum building)"
        elif h > 0:
            return W_MACD * 0.35, f"Index MACD histogram positive {h:.4f} (decelerating)"
        elif h > h_prev and h_prev < 0:
            return W_MACD * 0.20, f"Index MACD turning up from negative ({h:.4f})"
        return 0.0, f"Index MACD histogram negative and falling ({h:.4f})"

    def _score_ema_trend(self, df: object) -> Tuple[float, str]:
        """
        EMA 9 > EMA 21 on INDEX candles: bullish short-term structure.
        """
        closes = df["close"]
        ema9  = ta.trend.EMAIndicator(closes, window=9).ema_indicator()
        ema21 = ta.trend.EMAIndicator(closes, window=21).ema_indicator()
        e9, e21     = float(ema9.iloc[-1]),  float(ema21.iloc[-1])
        e9_p, e21_p = float(ema9.iloc[-2]),  float(ema21.iloc[-2])

        if e9 > e21:
            if e9_p <= e21_p:
                return W_EMA, f"Index EMA9×EMA21 golden cross! ({e9:.2f} > {e21:.2f})"
            gap_pct = (e9 - e21) / e21 * 100
            return W_EMA * 0.75, f"Index EMA9 > EMA21 bullish ({gap_pct:.2f}% gap)"
        elif e9 < e21 and e9 > e9_p:
            return W_EMA * 0.25, f"Index EMA9 rising toward EMA21 (gap closing)"
        return 0.0, f"Index EMA9 < EMA21 bearish ({e9:.2f} < {e21:.2f})"

    def _score_index_atr(self, df: object) -> Tuple[float, str]:
        """
        ATR on INDEX candles (60-min bars) as % of index price.

        For NIFTY (spot ~22,000), a 60-min ATR of 0.3–1.2% means the index
        moves ₹66–264 per hour — enough for options to appreciate but not so
        volatile that stop-losses whipsaw.

        Too low  (<0.3%) → market stagnant, options will not move
        Sweet spot (0.3–1.2%) → full score
        Moderate  (1.2–1.8%) → acceptable
        Too high  (>1.8%) → excessive whipsaw risk for intraday CE buys
        """
        atr_series = ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        ).average_true_range()
        atr   = float(atr_series.iloc[-1])
        price = float(df["close"].iloc[-1])
        if price == 0:
            return 0.0, "Index ATR: price is zero"

        atr_pct = (atr / price) * 100

        if 0.3 <= atr_pct <= 1.2:
            return W_ATR, f"Index ATR ideal {atr_pct:.2f}% (good intraday range)"
        elif 1.2 < atr_pct <= 1.8:
            return W_ATR * 0.5, f"Index ATR slightly elevated {atr_pct:.2f}%"
        elif 0.15 <= atr_pct < 0.3:
            return W_ATR * 0.3, f"Index ATR low {atr_pct:.2f}% — market may be slow"
        elif atr_pct < 0.15:
            return 0.0, f"Index ATR very low {atr_pct:.2f}% — stagnant, options won't move"
        return 0.0, f"Index ATR too high {atr_pct:.2f}% — excessive whipsaw risk"

    def _score_oi(self, quote: Dict, tradingsymbol: str) -> Tuple[float, str]:
        """OI buildup: rising OI + rising price = bullish confirmation."""
        oi           = quote.get("oi", 0) or 0
        oi_day_low   = quote.get("oi_day_low", 0) or 0
        volume       = quote.get("volume", 0) or 0
        last_price   = quote.get("last_price", 0) or 0

        if oi == 0:
            return 0.0, "OI data unavailable from Kite"

        if oi_day_low > 0:
            oi_increase_pct = (oi - oi_day_low) / oi_day_low * 100
            if oi_increase_pct >= 10 and last_price > 0:
                return W_OI, f"OI buildup +{oi_increase_pct:.1f}% from day low ({oi:,} contracts)"
            elif oi_increase_pct >= 5:
                return W_OI * 0.65, f"Moderate OI buildup +{oi_increase_pct:.1f}%"
            elif oi_increase_pct >= 0:
                return W_OI * 0.30, f"Stable OI ({oi_increase_pct:.1f}% from low)"
            else:
                return 0.0, f"OI falling {oi_increase_pct:.1f}% from high (long unwinding)"

        # Fallback: absolute OI as proxy for liquidity
        if oi >= 1_000_000:
            return W_OI * 0.5, f"High absolute OI ({oi:,}) — good liquidity"
        elif oi >= 100_000:
            return W_OI * 0.25, f"Moderate OI ({oi:,})"
        return 0.0, f"Low OI ({oi:,}) — illiquid option"

    def _score_volume(self, quote: Dict) -> Tuple[float, str]:
        """
        Option volume activity score using volume/OI turnover ratio.
        """
        volume = quote.get("volume", 0) or 0
        oi     = quote.get("oi", 0) or 0

        if volume == 0:
            return 0.0, "Volume data unavailable from Kite quote"
        if volume < 500:
            return 0.0, f"Very low option volume ({volume:,} lots) — illiquid"

        if oi > 0:
            turnover_ratio = volume / oi
            if turnover_ratio >= 0.20:
                return W_VOLUME, f"High option activity: volume/OI = {turnover_ratio:.0%} ({volume:,} lots)"
            elif turnover_ratio >= 0.10:
                return W_VOLUME * 0.65, f"Moderate option activity: volume/OI = {turnover_ratio:.0%}"
            elif turnover_ratio >= 0.03:
                return W_VOLUME * 0.30, f"Light option activity: volume/OI = {turnover_ratio:.0%}"
            return 0.0, f"Very low turnover {turnover_ratio:.1%} — stale OI, no fresh buying"

        # Fallback: absolute volume
        if volume >= 50_000:
            return W_VOLUME * 0.75, f"High absolute volume ({volume:,} lots)"
        elif volume >= 10_000:
            return W_VOLUME * 0.40, f"Moderate absolute volume ({volume:,} lots)"
        return W_VOLUME * 0.15, f"Low absolute volume ({volume:,} lots) — check liquidity"

    def _score_moneyness(
        self, strike: float, spot_price: Optional[float], lot_size: int
    ) -> Tuple[float, str]:
        """ATM ± 2 strikes preferred for balanced premium + delta."""
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

    def _score_pivot_proximity(
        self, spot_price: Optional[float], pivot_levels: Dict
    ) -> Tuple[float, str]:
        """
        Score based on how close the spot price is to key pivot levels.

        Best setup: price bouncing off S1 support (classic CE long entry).

        Scoring zones (as % distance from pivot level):
          ≤0.2% from S1 → full score (bounce at key support)
          ≤0.5% from S1 → 0.75 score (near S1, still high probability)
          ≤0.2% from PP → 0.5 score (at pivot, decent support)
          Between S1 and PP → 0.3 score (in support zone)
          ≤0.2% from R1 → 0.1 score (approaching resistance, risky)
          At or above R1 → 0 (extended, entering at resistance)
        """
        if not spot_price or not pivot_levels:
            return 0.0, "Pivot levels unavailable — skipping pivot proximity"

        pp = pivot_levels.get("pivot_pp")
        s1 = pivot_levels.get("pivot_s1")
        r1 = pivot_levels.get("pivot_r1")

        if not pp or not s1 or not r1:
            return 0.0, "Incomplete pivot data"

        def pct_from(level: float) -> float:
            return abs(spot_price - level) / level * 100 if level > 0 else 999

        dist_s1 = pct_from(s1)
        dist_pp = pct_from(pp)
        dist_r1 = pct_from(r1)

        # At/near R1 — approaching resistance
        if spot_price >= r1 * 0.998:
            return 0.0, f"At/above R1 {r1:.0f} — extended, entering at resistance"
        if dist_r1 <= 0.2:
            return W_PIVOT * 0.1, f"Near R1 resistance {r1:.0f} ({dist_r1:.2f}% away)"

        # At/near S1 — best CE entry zone
        if dist_s1 <= 0.2:
            return W_PIVOT, f"Spot at S1 support {s1:.0f} ({dist_s1:.2f}% away) — ideal CE entry"
        if dist_s1 <= 0.5:
            return W_PIVOT * 0.75, f"Spot near S1 support {s1:.0f} ({dist_s1:.2f}% away)"

        # At/near Pivot Point
        if dist_pp <= 0.2:
            return W_PIVOT * 0.50, f"Spot at Pivot PP {pp:.0f} ({dist_pp:.2f}% away)"

        # Between S1 and PP — in the support zone
        if s1 <= spot_price <= pp:
            return W_PIVOT * 0.30, f"Spot between S1 {s1:.0f} and PP {pp:.0f} — support zone"

        # Between PP and R1
        if pp < spot_price < r1:
            return W_PIVOT * 0.15, f"Spot between PP {pp:.0f} and R1 {r1:.0f}"

        return 0.0, f"Spot {spot_price:.0f} outside key pivot zones (S1={s1:.0f}, PP={pp:.0f}, R1={r1:.0f})"

    def _score_order_flow(self, depth: Dict) -> Tuple[float, str]:
        """
        Order book imbalance — real-time bid/ask depth from Kite.
        """
        if not depth:
            return W_ORDER_FLOW * 0.3, "Order book data unavailable — assuming neutral"

        imbalance  = depth.get("imbalance", 0.5)
        spread_pct = depth.get("spread_pct", 0)
        buy_qty    = depth.get("total_buy_qty", 0)
        sell_qty   = depth.get("total_sell_qty", 0)

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
            return W_ORDER_FLOW * liquidity_factor, \
                f"Strong buyer dominance: {buy_qty:,} bid vs {sell_qty:,} ask ({imbalance:.0%} buy){spread_note}"
        elif imbalance >= 0.60:
            return W_ORDER_FLOW * 0.75 * liquidity_factor, \
                f"Moderate buyer edge: {imbalance:.0%} buy side{spread_note}"
        elif imbalance >= 0.50:
            return W_ORDER_FLOW * 0.40 * liquidity_factor, \
                f"Slight buyer edge: {imbalance:.0%} buy side{spread_note}"
        elif imbalance >= 0.40:
            return W_ORDER_FLOW * 0.10, \
                f"Balanced order book: {imbalance:.0%} buy side{spread_note}"
        return 0.0, \
            f"Seller dominated: only {imbalance:.0%} buy side ({buy_qty:,} bid vs {sell_qty:,} ask){spread_note}"

    def _score_pcr(self, pcr: Optional[float]) -> Tuple[float, str]:
        """
        Put-Call Ratio scoring.

        PCR is a CONTRARIAN indicator:
          High PCR (≥1.2) → too many put buyers → market is over-hedged
                            → smart money fades the puts → bullish for CE
          Low  PCR (<0.7) → too many call buyers → crowd is over-bullish
                            → contrarian fade → bearish for CE

        Scoring:
          PCR ≥ 1.5  → very over-hedged, strongest CE buy signal → full score
          PCR ≥ 1.2  → over-hedged → 0.75 score
          PCR ≥ 0.9  → neutral → 0.40 score
          PCR < 0.9  → slightly call-heavy → 0.10 score
          PCR < 0.7  → over-bullish crowd → 0 pts (VETO 5 fires separately)
        """
        if pcr is None:
            return W_PCR * 0.3, "PCR unavailable — assuming neutral"

        if pcr >= 1.5:
            return W_PCR, f"PCR {pcr:.2f} — strongly over-hedged market (CE bullish signal)"
        elif pcr >= PCR_BULLISH:  # 1.2
            return W_PCR * 0.75, f"PCR {pcr:.2f} — over-hedged, contrarian CE buy zone"
        elif pcr >= PCR_NEUTRAL:  # 0.9
            return W_PCR * 0.40, f"PCR {pcr:.2f} — neutral market sentiment"
        elif pcr >= PCR_BEARISH:  # 0.7
            return W_PCR * 0.10, f"PCR {pcr:.2f} — slightly call-heavy, caution"
        return 0.0, f"PCR {pcr:.2f} — crowd over-bullish on calls (fade risk)"

    def _score_vix(self, vix: Optional[float]) -> Tuple[float, str]:
        """
        India VIX scoring.

        VIX measures implied volatility (option premium level):
          10–15 → ideal CE buying environment (affordable premium, good movement expected)
          15–18 → slightly elevated, acceptable
          >18   → options too expensive (VETO 4 fires separately)
          <10   → market too stagnant, no premium appreciation expected

        Scoring:
          10–15  → full score (ideal zone)
          15–18  → 0.5 score (slightly expensive but tradeable)
          8–10   → 0.25 score (a bit low, premium may not move much)
          <8     → 0 pts (market too stagnant)
          >18    → 0 pts (VETO 4 also fires)
        """
        if vix is None:
            return W_VIX * 0.3, "India VIX unavailable — assuming neutral"

        if VIX_IDEAL_LOW <= vix <= VIX_IDEAL_HIGH:  # 10–15
            return W_VIX, f"India VIX {vix:.1f} — ideal CE buying environment"
        elif VIX_IDEAL_HIGH < vix <= VIX_ELEVATED:  # 15–18
            return W_VIX * 0.5, f"India VIX {vix:.1f} — slightly elevated, options costlier"
        elif 8.0 <= vix < VIX_IDEAL_LOW:  # 8–10
            return W_VIX * 0.25, f"India VIX {vix:.1f} — low volatility, limited premium movement"
        elif vix < 8.0:
            return 0.0, f"India VIX {vix:.1f} — market too stagnant, options won't appreciate"
        return 0.0, f"India VIX {vix:.1f} — too high, option premiums very expensive (VETO)"

    def _score_llm(
        self,
        tradingsymbol: str,
        df: Optional[object],
        indicators: Optional[Dict],
        spot_price: Optional[float],
        underlying_token: int,
        daily_rows: Optional[List[Dict]] = None,
    ) -> Tuple[float, str, List[str]]:
        """
        Run LLM (Claude Haiku) multi-timeframe analysis on the underlying index.
        Returns (pts, summary_reason, full_reasoning_list).
        """
        if not self._llm.available:
            return 0.0, "LLM unavailable (ANTHROPIC_API_KEY not set)", []

        if df is None or indicators is None:
            return 0.0, "LLM skipped — no OHLCV data", []

        try:
            # Fetch weekly candles for full multi-TF context
            mtf = self._fetch_multi_timeframe(underlying_token)
            # Use pre-fetched daily_rows if provided (avoids redundant API call)
            if daily_rows:
                mtf["daily"] = daily_rows[-30:] if len(daily_rows) > 30 else daily_rows

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

    # ── Group combiner ────────────────────────────────────────────────────────

    @staticmethod
    def _combine_group(
        raw_scores: List[Tuple[float, float]],   # [(pts, max), ...]
        group_max: float,
        group_name: str,
    ) -> Tuple[float, str]:
        """
        Combine raw factor scores within one correlated group.
        Applies confirmation bonus (+15%) when all agree, conflict discount (−30%) when mixed.
        Caps at group_max.
        """
        if not raw_scores:
            return 0.0, "no factors"

        raw_sum   = sum(pts for pts, _ in raw_scores)
        n         = len(raw_scores)
        positives = sum(1 for pts, mx in raw_scores if mx > 0 and pts / mx >= 0.5)
        negatives = n - positives

        if positives == n:
            adjusted = raw_sum * 1.15
            note = f"confirmed ({positives}/{n} bullish, +15% bonus)"
        elif negatives == n:
            adjusted = raw_sum
            note = f"all bearish ({negatives}/{n}), no bonus"
        elif positives >= n * 0.67:
            adjusted = raw_sum * 1.07
            note = f"majority bullish ({positives}/{n}, +7% partial bonus)"
        elif negatives >= n * 0.67:
            adjusted = raw_sum * 0.85
            note = f"majority bearish ({negatives}/{n}, -15% conflict discount)"
        else:
            adjusted = raw_sum * 0.70
            note = f"mixed signals ({positives} bull vs {negatives} bear, -30% conflict discount)"

        capped = min(adjusted, group_max)
        logger.debug(
            f"Group '{group_name}': raw={raw_sum:.1f} adjusted={adjusted:.1f} "
            f"capped={capped:.1f}/{group_max} [{note}]"
        )
        return round(capped, 1), note

    # ── Veto gate evaluator ───────────────────────────────────────────────────

    @staticmethod
    def _apply_veto_gates(
        score: float,
        factors: Dict,
        depth: Dict,
        quote: Dict,
        moneyness_pct: Optional[float],
        vix: Optional[float],
        pcr: Optional[float],
    ) -> Tuple[float, List[str]]:
        """
        Apply five hard veto gates that can cap the final score.

        VETO 1 — Bear Trap Guard       (sellers dominate + OI falling)
        VETO 2 — Deep OTM Lock         (strike > 3.5% OTM)
        VETO 3 — Trend-Momentum Div.   (MACD + EMA both bearish)
        VETO 4 — Elevated VIX          (VIX > 18, options too expensive)
        VETO 5 — Bearish PCR           (PCR < 0.7, crowd over-bullish)
        """
        veto_notes = []
        capped = score

        # ── VETO 1: Bear Trap Guard ───────────────────────────────────────────
        imbalance  = depth.get("imbalance", 0.5) if depth else 0.5
        oi         = quote.get("oi", 0) or 0
        oi_day_low = quote.get("oi_day_low", 0) or 0
        oi_falling = oi_day_low > 0 and oi < oi_day_low * 1.01

        if imbalance < 0.40 and oi_falling:
            if capped > 40:
                veto_notes.append(
                    f"⚡ VETO 1 – Bear Trap Guard: sellers dominate "
                    f"({imbalance:.0%} buy) + OI unwinding → score capped at 40"
                )
                capped = min(capped, 40.0)

        # ── VETO 2: Deep OTM Lock ─────────────────────────────────────────────
        if moneyness_pct is not None and moneyness_pct > 3.5:
            if capped > 35:
                veto_notes.append(
                    f"⚡ VETO 2 – Deep OTM Lock: strike is {moneyness_pct:+.1f}% OTM "
                    f"(delta too low for intraday) → score capped at 35"
                )
                capped = min(capped, 35.0)

        # ── VETO 3: Trend-Momentum Divergence ────────────────────────────────
        macd_f = factors.get("MACD", {})
        ema_f  = factors.get("EMA Trend", {})
        macd_bearish = macd_f.get("pts", 0) == 0
        ema_bearish  = ema_f.get("pts", 0) == 0

        if macd_bearish and ema_bearish and capped >= 60:
            veto_notes.append(
                "⚡ VETO 3 – Trend-Momentum Divergence: Index MACD bearish + EMA bearish "
                "(falling knife risk) → score capped at 55"
            )
            capped = min(capped, 55.0)

        # ── VETO 4: Elevated VIX ──────────────────────────────────────────────
        if vix is not None and vix > VIX_ELEVATED and capped > 50:
            veto_notes.append(
                f"⚡ VETO 4 – Elevated VIX: India VIX {vix:.1f} > {VIX_ELEVATED} "
                f"(options overpriced, theta decay risk) → score capped at 50"
            )
            capped = min(capped, 50.0)

        # ── VETO 5: Bearish PCR ───────────────────────────────────────────────
        if pcr is not None and pcr < PCR_BEARISH and capped > 45:
            veto_notes.append(
                f"⚡ VETO 5 – Bearish PCR: PCR {pcr:.2f} < {PCR_BEARISH} "
                f"(crowd over-bullish on calls → fade risk) → score capped at 45"
            )
            capped = min(capped, 45.0)

        return round(capped, 1), veto_notes

    # ── Master scorer ─────────────────────────────────────────────────────────

    def score_instrument(
        self,
        tradingsymbol: str,
        instrument_token: int,
        strike: float,
        expiry,
        lot_size: int,
        underlying_token: int,
        underlying: str,
        spot_price: Optional[float],
        index_df=None,         # pre-fetched index hourly DataFrame (avoids redundant API calls)
        daily_rows=None,       # pre-fetched daily candles for pivot computation
        vix: Optional[float] = None,   # pre-fetched India VIX
        pcr: Optional[float] = None,   # pre-fetched PCR
        skip_llm: bool = False,        # True during batch /rank — LLM is on-demand only
    ) -> Dict:
        """
        Compute full win-probability score for one instrument.

        All momentum/trend indicators (RSI, MACD, EMA, ATR) run on index_df.
        Option-specific data (OI, Volume, Order Flow) comes from the option quote.
        PCR and VIX are market-wide and pre-fetched once in rank_instruments().
        """
        factors      = {}
        veto_notes   = []
        llm_reasoning = []

        # ── 1. Fetch option data (quote + depth) ───────────────────────────────
        quote = self._fetch_quote(tradingsymbol)
        depth = self.kite.get_market_depth(tradingsymbol)

        # ── 2. Use pre-fetched index DataFrame ────────────────────────────────
        # index_df is always the underlying index (NIFTY/BANKNIFTY), never the option
        df = index_df

        # ── 3. Compute indicators from INDEX candles ───────────────────────────
        indicators = None
        if df is not None and TA_AVAILABLE:
            try:
                closes = df["close"]
                rsi_s  = ta.momentum.RSIIndicator(closes, window=14).rsi()
                macd_o = ta.trend.MACD(closes, window_fast=12, window_slow=26, window_sign=9)
                indicators = {
                    "rsi":                 float(rsi_s.iloc[-1]),
                    "rsi_prev":            float(rsi_s.iloc[-2]),
                    "macd_histogram":      float(macd_o.macd_diff().iloc[-1]),
                    "macd_histogram_prev": float(macd_o.macd_diff().iloc[-2]),
                    "macd_line":           float(macd_o.macd().iloc[-1]),
                    "signal_line":         float(macd_o.macd_signal().iloc[-1]),
                    "current_price":       float(closes.iloc[-1]),
                    "price_trend_pct":     float(
                        (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6] * 100
                        if len(closes) >= 6 else 0
                    ),
                }
            except Exception:
                indicators = None

        # ── 4. Compute pivot levels from daily candles ─────────────────────────
        pivot_levels = _compute_pivot_levels(daily_rows or [])

        # ── 5. Raw factor scores ───────────────────────────────────────────────

        # Group A — Momentum (INDEX candles): raw pool = 30 pts
        if df is not None:
            rsi_pts,  rsi_reason  = self._score_rsi(df)
            macd_pts, macd_reason = self._score_macd(df)
        else:
            rsi_pts,  rsi_reason  = 0.0, "Index OHLCV unavailable"
            macd_pts, macd_reason = 0.0, "Index OHLCV unavailable"

        factors["RSI"]  = {"pts": round(rsi_pts,  1), "max": W_RSI,  "reason": rsi_reason}
        factors["MACD"] = {"pts": round(macd_pts, 1), "max": W_MACD, "reason": macd_reason}

        group_a_pts, group_a_note = self._combine_group(
            [(rsi_pts, W_RSI), (macd_pts, W_MACD)],
            group_max=W_RSI + W_MACD,
            group_name="A-Momentum",
        )

        # Group B — Trend Structure (INDEX candles): raw pool = 15 pts
        if df is not None:
            ema_pts, ema_reason = self._score_ema_trend(df)
            atr_pts, atr_reason = self._score_index_atr(df)
        else:
            ema_pts, ema_reason = 0.0, "Index OHLCV unavailable"
            atr_pts, atr_reason = 0.0, "Index OHLCV unavailable"

        factors["EMA Trend"] = {"pts": round(ema_pts, 1), "max": W_EMA, "reason": ema_reason}
        factors["Index ATR"] = {"pts": round(atr_pts, 1), "max": W_ATR, "reason": atr_reason}

        group_b_pts, group_b_note = self._combine_group(
            [(ema_pts, W_EMA), (atr_pts, W_ATR)],
            group_max=W_EMA + W_ATR,
            group_name="B-TrendStructure",
        )

        # Group C — Option Activity: raw pool = 15 pts
        oi_pts,  oi_reason  = self._score_oi(quote, tradingsymbol)
        vol_pts, vol_reason = self._score_volume(quote)

        factors["OI Buildup"] = {"pts": round(oi_pts,  1), "max": W_OI,     "reason": oi_reason}
        factors["Volume"]     = {"pts": round(vol_pts, 1), "max": W_VOLUME,  "reason": vol_reason}

        group_c_pts, group_c_note = self._combine_group(
            [(oi_pts, W_OI), (vol_pts, W_VOLUME)],
            group_max=W_OI + W_VOLUME,
            group_name="C-OptionActivity",
        )

        # Group D — Live Market: raw pool = 25 pts
        of_pts, of_reason = self._score_order_flow(depth)
        mn_pts, mn_reason = self._score_moneyness(strike, spot_price, lot_size)
        pv_pts, pv_reason = self._score_pivot_proximity(spot_price, pivot_levels)
        tm_pts, tm_reason = _time_bonus()

        factors["Order Flow"]      = {"pts": round(of_pts, 1), "max": W_ORDER_FLOW, "reason": of_reason}
        factors["Moneyness"]       = {"pts": round(mn_pts, 1), "max": W_MONEYNESS,  "reason": mn_reason}
        factors["Pivot Proximity"] = {"pts": round(pv_pts, 1), "max": W_PIVOT,      "reason": pv_reason}
        factors["Time Window"]     = {"pts": round(tm_pts, 1), "max": W_TIME,       "reason": tm_reason}

        # Group D uses simple sum + cap (orthogonal signals, no conflict logic)
        group_d_pts = min(of_pts + mn_pts + pv_pts + tm_pts, W_ORDER_FLOW + W_MONEYNESS + W_PIVOT + W_TIME)
        group_d_note = "orthogonal signals (no conflict logic)"

        # Group E — Market Sentiment (PCR + VIX): raw pool = 15 pts
        pcr_pts, pcr_reason = self._score_pcr(pcr)
        vix_pts, vix_reason = self._score_vix(vix)

        factors["PCR"]        = {"pts": round(pcr_pts, 1), "max": W_PCR, "reason": pcr_reason}
        factors["India VIX"]  = {"pts": round(vix_pts, 1), "max": W_VIX, "reason": vix_reason}

        group_e_pts, group_e_note = self._combine_group(
            [(pcr_pts, W_PCR), (vix_pts, W_VIX)],
            group_max=W_PCR + W_VIX,
            group_name="E-Sentiment",
        )

        # LLM — independent tiebreaker (5 pts)
        # Skipped during batch /rank to avoid Anthropic API costs on every scan.
        # Triggered on-demand via POST /rank/llm-analyze for individual instruments.
        if skip_llm:
            llm_pts, llm_reason, llm_reasoning = 0.0, "LLM skipped — use 'Analyse with AI' button", []
        else:
            llm_pts, llm_reason, llm_reasoning = self._score_llm(
                tradingsymbol=tradingsymbol,
                df=df,
                indicators=indicators,
                spot_price=spot_price,
                underlying_token=underlying_token,
                daily_rows=daily_rows,
            )
        factors["LLM Analysis"] = {"pts": round(llm_pts, 1), "max": W_LLM, "reason": llm_reason}

        # ── 6. Sum group scores ────────────────────────────────────────────────
        pre_llm_score = group_a_pts + group_b_pts + group_c_pts + group_d_pts + group_e_pts

        # ── 7. LLM tiebreaker / discount on technical groups ──────────────────
        llm_adjustment_note = ""
        llm_direction  = "HOLD"
        llm_confidence = 0.0
        try:
            if "LLM BUY" in llm_reason:
                llm_direction  = "BUY"
                llm_confidence = float(llm_reason.split("BUY ")[1].split("%")[0])
            elif "HOLD" in llm_reason:
                llm_direction  = "HOLD"
                llm_confidence = 50.0
        except Exception:
            pass

        tech_score = group_a_pts + group_b_pts
        if llm_direction == "HOLD" and tech_score >= 25 and llm_pts == 0:
            discount    = tech_score * 0.10
            group_a_pts = max(0, group_a_pts - discount * (group_a_pts / max(tech_score, 0.001)))
            group_b_pts = max(0, group_b_pts - discount * (group_b_pts / max(tech_score, 0.001)))
            llm_adjustment_note = "LLM disagrees with technical bullish signal → -10% TA discount"
        elif llm_direction == "BUY" and llm_confidence >= 70:
            llm_adjustment_note = f"LLM strongly agrees (BUY {llm_confidence:.0f}%) → tiebreaker bonus"

        total_pts = group_a_pts + group_b_pts + group_c_pts + group_d_pts + group_e_pts + llm_pts

        # ── 8. Apply hard veto gates ───────────────────────────────────────────
        moneyness_pct = (
            round((strike - spot_price) / spot_price * 100, 2) if spot_price else None
        )
        score_before_veto = round(min(100.0, max(0.0, total_pts)), 1)
        score, veto_notes = self._apply_veto_gates(
            score=score_before_veto,
            factors=factors,
            depth=depth,
            quote=quote,
            moneyness_pct=moneyness_pct,
            vix=vix,
            pcr=pcr,
        )

        # ── 9. Grade ───────────────────────────────────────────────────────────
        grade, label, win_prob = _grade(score)

        # Annotate factor dict with group membership
        factors["RSI"]["group"]           = "A-Momentum"
        factors["MACD"]["group"]          = "A-Momentum"
        factors["EMA Trend"]["group"]     = "B-TrendStructure"
        factors["Index ATR"]["group"]     = "B-TrendStructure"
        factors["OI Buildup"]["group"]    = "C-OptionActivity"
        factors["Volume"]["group"]        = "C-OptionActivity"
        factors["Order Flow"]["group"]    = "D-LiveMarket"
        factors["Moneyness"]["group"]     = "D-LiveMarket"
        factors["Pivot Proximity"]["group"] = "D-LiveMarket"
        factors["Time Window"]["group"]   = "D-LiveMarket"
        factors["PCR"]["group"]           = "E-Sentiment"
        factors["India VIX"]["group"]     = "E-Sentiment"
        factors["LLM Analysis"]["group"]  = "LLM"

        group_diagnosis = {
            "A-Momentum":       {"pts": round(group_a_pts, 1), "max": W_RSI + W_MACD,                              "note": group_a_note},
            "B-TrendStructure": {"pts": round(group_b_pts, 1), "max": W_EMA + W_ATR,                               "note": group_b_note},
            "C-OptionActivity": {"pts": round(group_c_pts, 1), "max": W_OI + W_VOLUME,                             "note": group_c_note},
            "D-LiveMarket":     {"pts": round(group_d_pts, 1), "max": W_ORDER_FLOW + W_MONEYNESS + W_PIVOT + W_TIME, "note": group_d_note},
            "E-Sentiment":      {"pts": round(group_e_pts, 1), "max": W_PCR + W_VIX,                               "note": group_e_note},
            "LLM":              {"pts": round(llm_pts,     1), "max": W_LLM,                                       "note": llm_adjustment_note or "independent"},
        }

        # ── 10. Live LTP for the option ───────────────────────────────────────
        last_price = (
            quote.get("last_price")
            or depth.get("best_bid")
            or self.kite.get_ltp([f"NFO:{tradingsymbol}"]).get(f"NFO:{tradingsymbol}")
        )

        # ── 11. Suggested entry / SL / target ────────────────────────────────
        sl_pct  = 3.0
        tgt_pct = 8.0
        entry   = round(last_price, 2) if last_price else None
        sl      = round(last_price * (1 - sl_pct / 100), 2) if last_price else None
        target  = round(last_price * (1 + tgt_pct / 100), 2) if last_price else None

        # ── 12. Top 3 reasons ─────────────────────────────────────────────────
        top_reasons = [
            f"{name}: {v['reason']}"
            for name, v in sorted(factors.items(), key=lambda x: x[1]["pts"], reverse=True)
        ][:3]
        if veto_notes:
            top_reasons = veto_notes + top_reasons[:2]

        return {
            "rank":              None,
            "instrument":        tradingsymbol,
            "instrument_token":  instrument_token,
            "strike":            strike,
            "expiry":            str(expiry),
            "lot_size":          lot_size,
            "last_price":        entry,
            "score":             score,
            "score_before_veto": score_before_veto,
            "grade":             grade,
            "win_probability":   win_prob,
            "win_label":         label,
            "suggested_entry":   entry,
            "suggested_sl":      sl,
            "suggested_target":  target,
            "top_reasons":       top_reasons,
            "factors":           factors,
            "group_diagnosis":   group_diagnosis,
            "veto_notes":        veto_notes,
            "llm_reasoning":     llm_reasoning,
            "order_flow": {
                "imbalance":      depth.get("imbalance"),
                "total_buy_qty":  depth.get("total_buy_qty"),
                "total_sell_qty": depth.get("total_sell_qty"),
                "best_bid":       depth.get("best_bid"),
                "best_ask":       depth.get("best_ask"),
                "spread_pct":     depth.get("spread_pct"),
            } if depth else None,
            "moneyness_pct": moneyness_pct,
            "spot_price":    round(spot_price, 2) if spot_price else None,
            "pivot_levels":  pivot_levels,
            "india_vix":     round(vix, 2) if vix is not None else None,
            "pcr":           round(pcr, 3) if pcr is not None else None,
            "data_available": df is not None,
            "llm_available":  self._llm.available,
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

        Pre-fetches shared data ONCE for all instruments:
          - index hourly DataFrame  (RSI/MACD/EMA/ATR)
          - daily candles           (pivot levels + LLM context)
          - India VIX               (market sentiment filter)
          - PCR                     (put-call ratio)

        This eliminates N redundant Kite API calls (one per instrument in v1).
        """
        import time
        t0 = time.monotonic()

        # ── Pre-fetch shared data (once for ALL instruments) ──────────────────
        if spot_price is None:
            spot_price = self._fetch_spot_price(underlying)
        logger.info(f"Spot price for {underlying}: {spot_price}")

        logger.info(f"Pre-fetching shared data for {underlying}...")
        index_df    = self._fetch_index_ohlcv(underlying_token)
        daily_rows  = self._fetch_daily_ohlcv(underlying_token)
        vix         = self._fetch_vix()
        pcr         = self._fetch_pcr(underlying)
        logger.info(
            f"Shared data ready: index_df={'✓' if index_df is not None else '✗'} "
            f"({len(index_df) if index_df is not None else 0} candles), "
            f"daily={len(daily_rows)} rows, "
            f"VIX={vix}, PCR={pcr}"
        )

        # ── Score each instrument ─────────────────────────────────────────────
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
                    underlying=underlying,
                    spot_price=spot_price,
                    index_df=index_df,      # pre-fetched
                    daily_rows=daily_rows,  # pre-fetched
                    vix=vix,                # pre-fetched
                    pcr=pcr,                # pre-fetched
                    skip_llm=True,          # LLM is on-demand only — use AI button per row
                )
                scored.append(result)
            except Exception as exc:
                logger.warning(f"Scoring failed for {symbol}: {exc}")
                continue

        # Sort descending by score, assign ranks
        scored.sort(key=lambda x: x["score"], reverse=True)
        for i, item in enumerate(scored, start=1):
            item["rank"] = i

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        return {
            "underlying":    underlying,
            "spot_price":    round(spot_price, 2) if spot_price else None,
            "india_vix":     round(vix, 2) if vix is not None else None,
            "pcr":           round(pcr, 3) if pcr is not None else None,
            "ranked":        scored[:max_results],
            "scanned":       len(instruments),
            "scan_time_ms":  elapsed_ms,
            "generated_at":  datetime.now(IST).isoformat(),
        }
