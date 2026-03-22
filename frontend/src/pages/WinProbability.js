import React, { useState, useCallback } from 'react';
import { rankAPI, kiteAPI } from '../services/api';

// ─── Constants ────────────────────────────────────────────────────────────────

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'NIFTYNXT50', 'MIDCPNIFTY'];

const GRADE_STYLES = {
  'A+': { bg: 'bg-emerald-100', text: 'text-emerald-800', border: 'border-emerald-300', bar: 'bg-emerald-500' },
  'A':  { bg: 'bg-green-100',   text: 'text-green-800',   border: 'border-green-300',   bar: 'bg-green-500'   },
  'B':  { bg: 'bg-yellow-100',  text: 'text-yellow-800',  border: 'border-yellow-300',  bar: 'bg-yellow-500'  },
  'C':  { bg: 'bg-orange-100',  text: 'text-orange-800',  border: 'border-orange-300',  bar: 'bg-orange-400'  },
  'D':  { bg: 'bg-red-100',     text: 'text-red-800',     border: 'border-red-300',     bar: 'bg-red-400'     },
};

const FACTOR_ICONS = {
  'RSI':          '📊',
  'MACD':         '📈',
  'Bollinger':    '📉',
  'EMA Trend':    '〰️',
  'ATR':          '⚡',
  'OI Buildup':   '🏗️',
  'Volume':       '📦',
  'Order Flow':   '🌊',
  'Moneyness':    '🎯',
  'Time Window':  '⏱️',
  'LLM Analysis': '🤖',
};

// ─── Sub-components ───────────────────────────────────────────────────────────

/** Thin horizontal progress bar */
const ScoreBar = ({ pts, max, barClass }) => {
  const pct = max > 0 ? Math.min(100, (pts / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${barClass}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-gray-500 w-10 text-right">{pts}/{max}</span>
    </div>
  );
};

/**
 * Order Flow visualiser — shows buyer vs seller dominance as a split bar.
 * imbalance = buy_qty / (buy_qty + sell_qty), range 0–1
 */
const OrderFlowBar = ({ orderFlow }) => {
  if (!orderFlow || orderFlow.imbalance == null) return null;

  const { imbalance, total_buy_qty, total_sell_qty, best_bid, best_ask, spread_pct } = orderFlow;
  const buyPct  = Math.round(imbalance * 100);
  const sellPct = 100 - buyPct;

  // Colour the label based on who dominates
  const dominance =
    imbalance >= 0.65 ? { label: 'Buyer dominated', color: 'text-emerald-700' } :
    imbalance >= 0.55 ? { label: 'Slight buyer edge', color: 'text-green-600' } :
    imbalance >= 0.45 ? { label: 'Balanced', color: 'text-yellow-600' } :
    imbalance >= 0.35 ? { label: 'Slight seller edge', color: 'text-orange-600' } :
                        { label: 'Seller dominated', color: 'text-red-600' };

  return (
    <div className="mt-3 bg-white rounded-lg border border-gray-100 px-3 py-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold text-gray-600">🌊 Order Flow (Live Depth)</span>
        <span className={`text-xs font-bold ${dominance.color}`}>{dominance.label}</span>
      </div>

      {/* Split bar: green = buyers, red = sellers */}
      <div className="flex h-3 rounded-full overflow-hidden gap-0.5">
        <div
          className="bg-emerald-400 rounded-l-full transition-all"
          style={{ width: `${buyPct}%` }}
          title={`Buy: ${total_buy_qty?.toLocaleString('en-IN')} qty`}
        />
        <div
          className="bg-red-400 rounded-r-full transition-all"
          style={{ width: `${sellPct}%` }}
          title={`Sell: ${total_sell_qty?.toLocaleString('en-IN')} qty`}
        />
      </div>

      {/* Stats row */}
      <div className="flex justify-between mt-1 text-xs text-gray-500">
        <span className="text-emerald-600 font-medium">
          Buy {buyPct}% ({total_buy_qty?.toLocaleString('en-IN') ?? '—'})
        </span>
        {spread_pct != null && (
          <span className={spread_pct > 1.0 ? 'text-red-500 font-medium' : 'text-gray-400'}>
            Spread {spread_pct.toFixed(2)}%{spread_pct > 1.0 ? ' ⚠️' : ''}
          </span>
        )}
        <span className="text-red-500 font-medium">
          Sell {sellPct}% ({total_sell_qty?.toLocaleString('en-IN') ?? '—'})
        </span>
      </div>

      {/* Bid / Ask prices */}
      {(best_bid != null || best_ask != null) && (
        <div className="flex justify-between mt-0.5 text-xs text-gray-400">
          <span>Bid ₹{best_bid?.toFixed(2) ?? '—'}</span>
          <span>Ask ₹{best_ask?.toFixed(2) ?? '—'}</span>
        </div>
      )}
    </div>
  );
};

/**
 * LLM Reasoning panel — renders the multi-timeframe reasoning bullets.
 * llmReasoning is an array of strings from Claude Haiku.
 */
const LLMReasoningPanel = ({ llmReasoning, llmFactor }) => {
  const [showFull, setShowFull] = useState(false);

  if (!llmReasoning || llmReasoning.length === 0) return null;

  // Show first 2 bullets collapsed, rest on expand
  const preview = llmReasoning.slice(0, 2);
  const rest    = llmReasoning.slice(2);

  const pts       = llmFactor?.pts ?? 0;
  const max       = llmFactor?.max ?? 5;
  const pct       = max > 0 ? pts / max : 0;
  const sentiment = pct >= 0.8 ? { label: 'BUY', cls: 'bg-emerald-100 text-emerald-700 border-emerald-200' }
                  : pct >= 0.5 ? { label: 'LEAN BUY', cls: 'bg-yellow-100 text-yellow-700 border-yellow-200' }
                  :              { label: 'HOLD/SKIP', cls: 'bg-gray-100 text-gray-600 border-gray-200' };

  return (
    <div className="mt-3 bg-white rounded-lg border border-blue-100 px-3 py-2">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-semibold text-gray-600">🤖 AI Multi-Timeframe Analysis</span>
        <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${sentiment.cls}`}>
          {sentiment.label} · {pts}/{max} pts
        </span>
      </div>

      <ul className="space-y-1">
        {preview.map((line, i) => (
          <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
            <span className="text-blue-400 mt-0.5 shrink-0">›</span>
            <span className="leading-snug">{line}</span>
          </li>
        ))}

        {showFull && rest.map((line, i) => (
          <li key={`r-${i}`} className="text-xs text-gray-600 flex items-start gap-1.5">
            <span className="text-blue-400 mt-0.5 shrink-0">›</span>
            <span className="leading-snug">{line}</span>
          </li>
        ))}
      </ul>

      {rest.length > 0 && (
        <button
          onClick={() => setShowFull(v => !v)}
          className="mt-1.5 text-xs text-blue-500 hover:text-blue-700 font-medium"
        >
          {showFull ? '▲ Show less' : `▼ +${rest.length} more insights`}
        </button>
      )}
    </div>
  );
};

/** Factor breakdown rows inside the expanded card */
const FactorsTable = ({ factors }) => (
  <div className="mt-3 border-t border-gray-100 pt-3 space-y-2">
    {Object.entries(factors).map(([name, { pts, max, reason }]) => {
      const pct     = max > 0 ? pts / max : 0;
      const barCls  = pct >= 0.8 ? 'bg-emerald-400' : pct >= 0.5 ? 'bg-yellow-400' : 'bg-red-300';
      return (
        <div key={name}>
          <div className="flex items-center justify-between mb-0.5">
            <span className="text-xs font-medium text-gray-600">
              {FACTOR_ICONS[name] || '•'} {name}
            </span>
            <span className={`text-xs font-mono ${pct >= 0.6 ? 'text-emerald-600' : pct >= 0.3 ? 'text-yellow-600' : 'text-red-500'}`}>
              {pts}/{max}
            </span>
          </div>
          <ScoreBar pts={pts} max={max} barClass={barCls} />
          <p className="text-xs text-gray-400 mt-0.5 leading-snug">{reason}</p>
        </div>
      );
    })}
  </div>
);

/** Single instrument card */
const InstrumentCard = ({ item, rank }) => {
  const [expanded, setExpanded] = useState(false);
  const style = GRADE_STYLES[item.grade] || GRADE_STYLES['D'];

  const factorCount = item.factors ? Object.keys(item.factors).length : 11;

  return (
    <div className={`rounded-xl border ${style.border} ${style.bg} p-4 transition-shadow hover:shadow-md`}>

      {/* ── Header row ────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-3">

        {/* Rank badge */}
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-white border border-gray-200 flex items-center justify-center">
          <span className="text-xs font-bold text-gray-600">#{rank}</span>
        </div>

        {/* Instrument info */}
        <div className="flex-1 min-w-0">
          <p className="font-mono font-bold text-gray-900 text-sm truncate">{item.instrument}</p>
          <p className="text-xs text-gray-500 mt-0.5">
            Strike ₹{item.strike?.toLocaleString('en-IN')}
            {item.expiry ? ` · ${new Date(item.expiry).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}` : ''}
            {item.moneyness_pct != null ? (
              <span className={item.moneyness_pct >= 0 ? ' · text-orange-500' : ' · text-blue-500'}>
                {' '}{item.moneyness_pct > 0 ? `+${item.moneyness_pct.toFixed(1)}% OTM` : `${item.moneyness_pct.toFixed(1)}% ITM`}
              </span>
            ) : null}
          </p>
        </div>

        {/* Grade badge */}
        <div className={`flex-shrink-0 px-3 py-1 rounded-full text-sm font-extrabold ${style.text} bg-white border ${style.border}`}>
          {item.grade}
        </div>
      </div>

      {/* ── Score bar ─────────────────────────────────────────── */}
      <div className="mt-3">
        <div className="flex justify-between text-xs mb-1">
          <span className={`font-semibold ${style.text}`}>Win Probability: {item.win_probability}</span>
          <span className="text-gray-500">Score {item.score}/100</span>
        </div>
        <div className="h-2.5 bg-white rounded-full overflow-hidden border border-gray-200">
          <div
            className={`h-full rounded-full transition-all ${style.bar}`}
            style={{ width: `${item.score}%` }}
          />
        </div>
      </div>

      {/* ── Price row ─────────────────────────────────────────── */}
      {item.last_price != null && (
        <div className="mt-3 grid grid-cols-3 gap-2 text-center">
          <div className="bg-white rounded-lg py-1.5 border border-gray-100">
            <p className="text-xs text-gray-400">Entry</p>
            <p className="text-sm font-bold text-gray-800">₹{item.suggested_entry?.toFixed(2)}</p>
          </div>
          <div className="bg-white rounded-lg py-1.5 border border-red-100">
            <p className="text-xs text-red-400">Stop-Loss</p>
            <p className="text-sm font-bold text-red-600">₹{item.suggested_sl?.toFixed(2)}</p>
          </div>
          <div className="bg-white rounded-lg py-1.5 border border-green-100">
            <p className="text-xs text-green-500">Target</p>
            <p className="text-sm font-bold text-green-700">₹{item.suggested_target?.toFixed(2)}</p>
          </div>
        </div>
      )}

      {/* ── Order Flow (live bid/ask imbalance) ───────────────── */}
      <OrderFlowBar orderFlow={item.order_flow} />

      {/* ── LLM multi-timeframe reasoning ─────────────────────── */}
      <LLMReasoningPanel
        llmReasoning={item.llm_reasoning}
        llmFactor={item.factors?.['LLM Analysis']}
      />

      {/* ── Top reasons ───────────────────────────────────────── */}
      {item.top_reasons?.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {item.top_reasons.map((r, i) => (
            <li key={i} className="text-xs text-gray-600 flex items-start gap-1">
              <span className="text-gray-400 mt-0.5">›</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>
      )}

      {/* ── Expand / collapse factor table ────────────────────── */}
      <button
        onClick={() => setExpanded(v => !v)}
        className="mt-3 w-full text-xs font-medium text-blue-600 hover:text-blue-800 flex items-center justify-center gap-1"
      >
        {expanded ? `▲ Hide factor breakdown` : `▼ Show all ${factorCount} factors`}
      </button>

      {expanded && item.factors && <FactorsTable factors={item.factors} />}
    </div>
  );
};

// ─── Main page ────────────────────────────────────────────────────────────────

const WinProbability = () => {
  const [underlying, setUnderlying]   = useState('NIFTY');
  const [maxResults, setMaxResults]   = useState(10);
  const [loading, setLoading]         = useState(false);
  const [data, setData]               = useState(null);
  const [error, setError]             = useState('');
  const [tokenExpired, setTokenExpired] = useState(false);
  const [lastFetch, setLastFetch]     = useState(null);

  const fetchRanking = useCallback(async () => {
    setLoading(true);
    setError('');
    setTokenExpired(false);
    try {
      const res = await rankAPI.getRanking(underlying, maxResults);
      setData(res.data);
      setLastFetch(new Date());
    } catch (err) {
      const detail = err.response?.data?.detail || err.message || 'Failed to fetch ranking';
      // Detect Kite token expiry (403 with KITE_TOKEN_EXPIRED prefix)
      if (err.response?.status === 403 && detail.includes('KITE_TOKEN_EXPIRED')) {
        setTokenExpired(true);
        setError('');
      } else {
        setError(detail);
      }
    } finally {
      setLoading(false);
    }
  }, [underlying, maxResults]);

  const handleRelinkKite = async () => {
    try {
      const res = await kiteAPI.getLoginUrl();
      if (res.data?.login_url) {
        window.location.href = res.data.login_url;
      }
    } catch {
      // Fallback: go to dashboard where the re-link button is always visible
      window.location.href = '/dashboard';
    }
  };

  // ── Helpers ──────────────────────────────────────────────────────────────
  const gradeCount = (grade) =>
    data?.ranked?.filter(i => i.grade === grade).length || 0;

  // Check if any result has LLM data
  const hasLLM = data?.ranked?.some(i => i.llm_reasoning?.length > 0);

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

      {/* ── Page header ───────────────────────────────────────────────── */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold text-gray-900">Win Probability Ranker</h1>
        <p className="mt-1 text-sm text-gray-500">
          Ranks call options by intraday profit probability using 11 factors —
          RSI, MACD, Bollinger Bands, EMA trend, ATR, OI buildup, Volume,{' '}
          <span className="text-blue-600 font-medium">live Order Flow (bid/ask depth)</span>,
          Moneyness, Time window, and{' '}
          <span className="text-purple-600 font-medium">AI multi-timeframe analysis</span>.
        </p>
      </div>

      {/* ── Controls ──────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 mb-6">
        <div className="flex flex-wrap items-end gap-4">

          {/* Underlying selector */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Underlying Index</label>
            <div className="flex gap-2">
              {UNDERLYINGS.map(u => (
                <button
                  key={u}
                  onClick={() => setUnderlying(u)}
                  className={`px-3 py-1.5 rounded-md text-sm font-semibold border transition-colors ${
                    underlying === u
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400 hover:text-blue-600'
                  }`}
                >
                  {u}
                </button>
              ))}
            </div>
          </div>

          {/* Result count */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Top N results</label>
            <select
              value={maxResults}
              onChange={e => setMaxResults(Number(e.target.value))}
              className="border border-gray-300 rounded-md px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {[5, 10, 15].map(n => (
                <option key={n} value={n}>Top {n}</option>
              ))}
            </select>
          </div>

          {/* Fetch button */}
          <button
            onClick={fetchRanking}
            disabled={loading}
            className="ml-auto px-5 py-2 rounded-lg font-semibold text-sm bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white flex items-center gap-2 transition-colors"
          >
            {loading
              ? <><span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" /> Scanning…</>
              : '🔍 Rank Instruments'
            }
          </button>
        </div>

        {/* Factor legend */}
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
          {[
            { grade: 'A+', label: 'Very High (>80%)' },
            { grade: 'A',  label: 'High (65–80%)' },
            { grade: 'B',  label: 'Moderate (50–65%)' },
            { grade: 'C',  label: 'Low (35–50%)' },
            { grade: 'D',  label: 'Very Low (<35%)' },
          ].map(({ grade, label }) => {
            const s = GRADE_STYLES[grade];
            return (
              <div key={grade} className="flex items-center gap-1.5 text-xs text-gray-500">
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold ${s.bg} ${s.text} border ${s.border}`}>
                  {grade}
                </span>
                {label}
              </div>
            );
          })}
        </div>

        {/* Factor legend pills */}
        <div className="mt-3 pt-3 border-t border-gray-100 flex flex-wrap gap-2">
          {[
            { icon: '📊', label: 'Technical Signals', sub: 'RSI · MACD · BB · EMA · ATR', cls: 'bg-blue-50 border-blue-100 text-blue-700' },
            { icon: '🏗️', label: 'Option Activity', sub: 'OI Buildup · Volume/OI', cls: 'bg-purple-50 border-purple-100 text-purple-700' },
            { icon: '🌊', label: 'Order Flow', sub: 'Live bid/ask depth imbalance', cls: 'bg-emerald-50 border-emerald-100 text-emerald-700' },
            { icon: '🤖', label: 'AI Analysis', sub: 'Claude Haiku multi-timeframe', cls: 'bg-indigo-50 border-indigo-100 text-indigo-700' },
          ].map(({ icon, label, sub, cls }) => (
            <div key={label} className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs ${cls}`}>
              <span>{icon}</span>
              <div>
                <span className="font-semibold">{label}</span>
                <span className="ml-1 opacity-70">{sub}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Error banner ──────────────────────────────────────────────── */}
      {error && (
        <div className="mb-4 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
          ✗ {error}
        </div>
      )}

      {/* ── Kite token expired banner ─────────────────────────────────── */}
      {tokenExpired && (
        <div className="mb-4 px-5 py-4 rounded-xl bg-amber-50 border border-amber-300 flex items-start gap-4">
          <span className="text-2xl shrink-0">🔑</span>
          <div className="flex-1">
            <p className="text-sm font-bold text-amber-800">Kite Session Expired</p>
            <p className="text-sm text-amber-700 mt-0.5">
              Your Kite access token has expired. Kite tokens reset every day — you need to
              re-link your account to fetch live market data.
            </p>
            <button
              onClick={handleRelinkKite}
              className="mt-3 px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold transition-colors"
            >
              🔗 Re-link Kite Account Now
            </button>
          </div>
        </div>
      )}

      {/* ── Empty state ───────────────────────────────────────────────── */}
      {!loading && !data && !error && !tokenExpired && (
        <div className="text-center py-20 text-gray-400">
          <div className="text-5xl mb-4">🎯</div>
          <p className="text-lg font-medium">Select an underlying and click "Rank Instruments"</p>
          <p className="text-sm mt-1">
            The engine scores each call option across 11 factors — including live order flow and AI analysis — then ranks by win probability.
          </p>
        </div>
      )}

      {/* ── Results ───────────────────────────────────────────────────── */}
      {data && (
        <>
          {/* Summary bar */}
          <div className="bg-white rounded-xl shadow-sm border border-gray-100 px-5 py-3 mb-5 flex flex-wrap items-center gap-5">
            <div>
              <p className="text-xs text-gray-400 uppercase tracking-wide">Underlying</p>
              <p className="text-base font-bold text-gray-800">{data.underlying}</p>
            </div>
            {data.spot_price && (
              <div>
                <p className="text-xs text-gray-400 uppercase tracking-wide">Spot Price</p>
                <p className="text-base font-bold text-gray-800">
                  ₹{data.spot_price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                </p>
              </div>
            )}
            <div>
              <p className="text-xs text-gray-400 uppercase tracking-wide">Scanned</p>
              <p className="text-base font-bold text-gray-800">{data.scanned} instruments</p>
            </div>
            <div>
              <p className="text-xs text-gray-400 uppercase tracking-wide">Scan Time</p>
              <p className="text-base font-bold text-gray-800">{(data.scan_time_ms / 1000).toFixed(1)}s</p>
            </div>

            {/* AI badge */}
            {hasLLM && (
              <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-indigo-50 border border-indigo-100">
                <span className="text-xs">🤖</span>
                <span className="text-xs font-semibold text-indigo-700">AI Enhanced</span>
              </div>
            )}

            {/* Grade distribution mini-pills */}
            <div className="ml-auto flex gap-1.5 flex-wrap">
              {['A+', 'A', 'B', 'C', 'D'].map(g => {
                const cnt = gradeCount(g);
                if (cnt === 0) return null;
                const s = GRADE_STYLES[g];
                return (
                  <span key={g} className={`px-2 py-0.5 rounded-full text-xs font-bold ${s.bg} ${s.text} border ${s.border}`}>
                    {g} ×{cnt}
                  </span>
                );
              })}
            </div>

            {lastFetch && (
              <p className="text-xs text-gray-400 w-full mt-0.5">
                Last updated: {lastFetch.toLocaleTimeString('en-IN')}
              </p>
            )}
          </div>

          {/* Ranked cards */}
          {data.ranked.length === 0 ? (
            <div className="text-center py-16 text-gray-400">
              <p className="text-lg">No instruments found for {data.underlying}.</p>
              <p className="text-sm mt-1">Check that your Kite account is linked and markets are open.</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {data.ranked.map((item) => (
                <InstrumentCard key={item.instrument} item={item} rank={item.rank} />
              ))}
            </div>
          )}

          {/* Disclaimer */}
          <div className="mt-6 px-4 py-3 rounded-lg bg-gray-50 border border-gray-200 text-xs text-gray-500 leading-relaxed">
            <strong>Disclaimer:</strong> Win probability scores are based on technical analysis, live order book data,
            and AI-driven multi-timeframe analysis. They do not guarantee profits.
            Options trading carries significant risk including total loss of premium.
            Always apply your own judgment and risk management. This tool is for informational purposes only.
          </div>
        </>
      )}
    </div>
  );
};

export default WinProbability;
