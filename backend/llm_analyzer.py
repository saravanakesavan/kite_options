"""
LLM Analyzer — multi-timeframe Claude analysis for call-option entries.

Data the LLM now receives:
  1. Weekly candles  (last 20 weeks)  — major trend, key S/R zones
  2. Daily candles   (last 30 days)   — swing highs/lows, short-term structure
  3. Hourly candles  (last 20 bars)   — entry timing, momentum
  4. Technical indicators             — RSI, MACD, price trend
  5. Support/Resistance levels        — pivot points, swing H/L, MA20/50

Design:
  - temperature=0, JSON-only output.
  - Falls back to HOLD when API key missing or call fails.
  - Blended 40/60 with rule-based score in SignalEngine.
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# System prompt — multi-timeframe edition
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a professional options trader and quantitative analyst
specialising in Indian equity index call options (NIFTY / BANKNIFTY / SENSEX CE).

You perform TOP-DOWN, MULTI-TIMEFRAME analysis before deciding on any entry:
  1. WEEKLY timeframe  → identify the dominant trend (bull / bear / sideways)
                         and major support/resistance zones
  2. DAILY timeframe   → locate recent swing highs/lows, confirm trend,
                         check if price is approaching a key level
  3. HOURLY timeframe  → find the precise entry trigger (RSI, MACD crossover)
  4. KEY LEVELS        → pivot points, swing H/L, MA20/MA50 — DO NOT buy into
                         a strong resistance; wait for a confirmed break or a
                         pullback to strong support

Critical rules for call-option entries:
  - WEEKLY trend must be UP or at worst sideways — avoid buying calls in a
    clear weekly downtrend (lower highs, lower lows week after week)
  - Price must be ABOVE the daily MA20, or rebounding from it with bullish
    hourly confirmation; buying below MA50 needs very strong RSI/MACD signal
  - Strong resistance within 1% of current price = HOLD; wait for breakout
  - Strong support within 0.5% below current price = favourable risk/reward
  - RSI(14) hourly: oversold (<30) + recovering slope = good entry signal
  - MACD histogram: fresh zero-line crossover (negative → positive) is the
    strongest intraday entry trigger
  - Avoid entries after 13:00 IST — time decay accelerates and liquidity drops
  - When in doubt, HOLD.  A missed trade costs nothing; a wrong one costs money.

Output rules:
  1. Reply ONLY with a valid JSON object — no markdown, no preamble.
  2. JSON must have exactly these keys:
       "direction"  : "BUY" or "HOLD"
       "confidence" : integer 0–100
       "reasoning"  : list of 4–6 short strings, each covering a different
                      timeframe or factor (weekly trend, daily S/R, hourly
                      trigger, key level proximity, risk assessment)

Confidence guide:
  85–100 : weekly + daily + hourly all aligned, price near support not resistance
  65–84  : strong hourly signal, trend supportive, no major resistance overhead
  45–64  : mixed signals — lean HOLD unless RSI/MACD overwhelmingly agree
  0–44   : HOLD — weekly bearish, price at resistance, or signals conflicting
"""


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_candles(rows: List[Dict], max_rows: int = 20, interval: str = "hourly") -> str:
    """Compact OHLCV table for the LLM prompt."""
    rows = rows[-max_rows:]
    header = f"{'date/time':<18} | {'open':>8} | {'high':>8} | {'low':>8} | {'close':>8} | {'volume':>8}"
    sep    = "-" * len(header)
    lines  = [header, sep]
    for r in rows:
        dt = r.get("datetime") or r.get("date", "")
        if hasattr(dt, "strftime"):
            dt = dt.strftime("%Y-%m-%d" if interval != "hourly" else "%m-%d %H:%M")
        else:
            dt = str(dt)[:16]
        lines.append(
            f"{dt:<18} | {r['open']:>8.2f} | {r['high']:>8.2f} | {r['low']:>8.2f} "
            f"| {r['close']:>8.2f} | {r.get('volume', 0):>8}"
        )
    return "\n".join(lines)


def _fmt_sr(sr: Dict) -> str:
    """Format support/resistance levels as a readable block."""
    if not sr:
        return "  (not available)"
    lines = []
    if sr.get("pivot_pp"):
        lines.append(
            f"  Pivot PP={sr['pivot_pp']}  R1={sr['pivot_r1']}  R2={sr['pivot_r2']}"
            f"  S1={sr['pivot_s1']}  S2={sr['pivot_s2']}"
        )
    if sr.get("ma_20") or sr.get("ma_50"):
        lines.append(
            f"  MA20={sr.get('ma_20','?')}  MA50={sr.get('ma_50','?')}"
        )
    if sr.get("swing_highs"):
        lines.append(f"  Swing highs (resistance): {sr['swing_highs']}")
    if sr.get("swing_lows"):
        lines.append(f"  Swing lows  (support):    {sr['swing_lows']}")
    if sr.get("period_high") or sr.get("period_low"):
        lines.append(
            f"  Period high={sr.get('period_high','?')}  "
            f"Period low={sr.get('period_low','?')}"
        )
    return "\n".join(lines) if lines else "  (not available)"


def _build_user_prompt(
    tradingsymbol: str,
    ohlcv_rows:    List[Dict],
    indicators:    Dict,
    spot_price:    float,
    daily_rows:    List[Dict],
    weekly_rows:   List[Dict],
    support_resistance: Dict,
) -> str:
    now      = datetime.now()
    time_str = now.strftime("%H:%M IST, %A %d %b %Y")

    ind       = indicators
    rsi_now   = ind.get("rsi", 0)
    rsi_prev  = ind.get("rsi_prev", 0)
    hist_now  = ind.get("macd_histogram", 0)
    hist_prev = ind.get("macd_histogram_prev", 0)
    trend_pct = ind.get("price_trend_pct", 0)
    cur_price = ind.get("current_price", 0)

    # Weekly trend summary from last 4 weekly closes
    weekly_summary = "N/A"
    if weekly_rows and len(weekly_rows) >= 4:
        w = weekly_rows
        closes  = [r["close"] for r in w[-4:]]
        highs   = [r["high"]  for r in w[-4:]]
        lows    = [r["low"]   for r in w[-4:]]
        wk_high = max(highs)
        wk_low  = min(lows)
        trend   = "UPTREND" if closes[-1] > closes[0] else "DOWNTREND"
        weekly_summary = (
            f"{trend} over last 4 weeks | "
            f"Range: {wk_low:.2f}–{wk_high:.2f} | "
            f"Last close: {closes[-1]:.2f}"
        )

    parts = [
        f"Instrument   : {tradingsymbol}",
        f"Current time : {time_str}",
        f"Spot price   : {spot_price:.2f}",
        "",
        "=== WEEKLY TREND (last 4 weeks) ===",
        weekly_summary,
    ]

    if weekly_rows:
        parts += [
            "",
            f"=== WEEKLY CANDLES — last {min(20, len(weekly_rows))} weeks (oldest → newest) ===",
            _fmt_candles(weekly_rows, max_rows=20, interval="weekly"),
        ]

    parts += [
        "",
        "=== KEY SUPPORT / RESISTANCE LEVELS ===",
        _fmt_sr(support_resistance),
    ]

    if daily_rows:
        parts += [
            "",
            f"=== DAILY CANDLES — last {min(20, len(daily_rows))} sessions (oldest → newest) ===",
            _fmt_candles(daily_rows, max_rows=20, interval="daily"),
        ]

    parts += [
        "",
        "=== TECHNICAL INDICATORS (latest hourly candle) ===",
        f"RSI (14)         : {rsi_now:.1f}  (prev: {rsi_prev:.1f})",
        f"MACD histogram   : {hist_now:.4f}  (prev: {hist_prev:.4f})",
        f"MACD line        : {ind.get('macd_line', 0):.4f}",
        f"Signal line      : {ind.get('signal_line', 0):.4f}",
        f"Price trend (6h) : {trend_pct:.2f}%",
        f"Current price    : {cur_price:.2f}",
        "",
        f"=== HOURLY CANDLES — last {min(20, len(ohlcv_rows))} bars (oldest → newest) ===",
        _fmt_candles(ohlcv_rows, max_rows=20, interval="hourly"),
        "",
        "Respond with JSON only.",
    ]

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# LLMAnalyzer class
# ─────────────────────────────────────────────────────────────────────────────

class LLMAnalyzer:
    """
    Multi-timeframe Claude analysis for call-option entry signals.

    Usage:
        analyzer = LLMAnalyzer()
        result = analyzer.analyze(
            tradingsymbol="NIFTY25JAN24000CE",
            ohlcv_rows=[...],            # hourly candles from kite
            indicators={...},            # from SignalEngine._compute_indicators()
            spot_price=24450.0,
            daily_rows=[...],            # daily candles (last 30)
            weekly_rows=[...],           # weekly candles (last 20)
            support_resistance={...},    # from SignalEngine._compute_support_resistance()
        )
    """

    def __init__(self):
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._client  = None
        if self._api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
                logger.info("LLMAnalyzer: Anthropic client initialised (multi-TF mode)")
            except ImportError:
                logger.warning("LLMAnalyzer: 'anthropic' package not installed")
        else:
            logger.warning("LLMAnalyzer: ANTHROPIC_API_KEY not set — LLM analysis disabled")

    @property
    def available(self) -> bool:
        return self._client is not None

    def analyze(
        self,
        tradingsymbol:      str,
        ohlcv_rows:         List[Dict],
        indicators:         Dict,
        spot_price:         float = 0.0,
        daily_rows:         Optional[List[Dict]] = None,
        weekly_rows:        Optional[List[Dict]] = None,
        support_resistance: Optional[Dict] = None,
    ) -> Dict:
        """
        Run multi-timeframe Claude analysis.

        Returns:
            direction   : "BUY" or "HOLD"
            confidence  : 0–100
            reasoning   : list[str]
            available   : bool
        """
        _fallback = {
            "direction": "HOLD",
            "confidence": 0,
            "reasoning": ["LLM unavailable — API key missing or package not installed"],
            "available": False,
        }

        if not self.available:
            return _fallback

        if not ohlcv_rows or len(ohlcv_rows) < 5:
            return {
                "direction": "HOLD",
                "confidence": 0,
                "reasoning": ["Insufficient hourly candle history for LLM analysis"],
                "available": True,
            }

        prompt = _build_user_prompt(
            tradingsymbol=tradingsymbol,
            ohlcv_rows=ohlcv_rows,
            indicators=indicators,
            spot_price=spot_price,
            daily_rows=daily_rows  or [],
            weekly_rows=weekly_rows or [],
            support_resistance=support_resistance or {},
        )

        try:
            message = self._client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                temperature=0,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = message.content[0].text.strip()

            # Strip markdown fences if model adds them
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]

            parsed     = json.loads(raw_text)
            direction  = str(parsed.get("direction", "HOLD")).upper()
            confidence = int(parsed.get("confidence", 0))
            reasoning  = list(parsed.get("reasoning", []))

            if direction not in ("BUY", "HOLD"):
                direction = "HOLD"
            confidence = max(0, min(100, confidence))

            logger.info(
                f"LLM [{tradingsymbol}]: {direction} confidence={confidence}% "
                f"weekly={len(weekly_rows or [])}w daily={len(daily_rows or [])}d "
                f"hourly={len(ohlcv_rows)}h"
            )
            return {
                "direction":  direction,
                "confidence": confidence,
                "reasoning":  reasoning,
                "available":  True,
            }

        except json.JSONDecodeError as e:
            logger.error(f"LLMAnalyzer JSON parse error [{tradingsymbol}]: {e}")
        except Exception as e:
            logger.error(f"LLMAnalyzer API error [{tradingsymbol}]: {e}")

        return {**_fallback, "available": True}
