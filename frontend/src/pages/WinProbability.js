import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { rankAPI, kiteAPI } from '../services/api';

// ─── Constants ────────────────────────────────────────────────────────────────

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'NIFTYNXT50', 'MIDCPNIFTY'];

const GRADE_STYLES = {
  'A+': { bg: 'bg-emerald-100', text: 'text-emerald-800', border: 'border-emerald-300', bar: 'bg-emerald-500', dot: 'bg-emerald-500' },
  'A':  { bg: 'bg-green-100',   text: 'text-green-800',   border: 'border-green-300',   bar: 'bg-green-500',   dot: 'bg-green-500'   },
  'B':  { bg: 'bg-yellow-100',  text: 'text-yellow-800',  border: 'border-yellow-300',  bar: 'bg-yellow-500',  dot: 'bg-yellow-500'  },
  'C':  { bg: 'bg-orange-100',  text: 'text-orange-800',  border: 'border-orange-300',  bar: 'bg-orange-400',  dot: 'bg-orange-400'  },
  'D':  { bg: 'bg-red-100',     text: 'text-red-800',     border: 'border-red-300',     bar: 'bg-red-400',     dot: 'bg-red-400'     },
  'N/A':{ bg: 'bg-gray-100',    text: 'text-gray-500',    border: 'border-gray-300',    bar: 'bg-gray-300',    dot: 'bg-gray-300'    },
};

const GROUP_META = {
  'A-Momentum':       { label: 'Momentum (Index)',        icon: '🚀', color: 'blue'   },
  'B-TrendStructure': { label: 'Trend Structure (Index)', icon: '📐', color: 'purple' },
  'C-OptionActivity': { label: 'Option Activity',         icon: '🏗️', color: 'amber'  },
  'D-LiveMarket':     { label: 'Live Market',             icon: '🌊', color: 'teal'   },
  'E-Sentiment':      { label: 'Market Sentiment',        icon: '🌡️', color: 'rose'   },
  'LLM':              { label: 'AI Analysis',             icon: '🤖', color: 'indigo' },
};

const COLOR_CLASSES = {
  blue:   { bg: 'bg-blue-50',   border: 'border-blue-200',   text: 'text-blue-700',   bar: 'bg-blue-400'   },
  purple: { bg: 'bg-purple-50', border: 'border-purple-200', text: 'text-purple-700', bar: 'bg-purple-400' },
  amber:  { bg: 'bg-amber-50',  border: 'border-amber-200',  text: 'text-amber-700',  bar: 'bg-amber-400'  },
  teal:   { bg: 'bg-teal-50',   border: 'border-teal-200',   text: 'text-teal-700',   bar: 'bg-teal-400'   },
  rose:   { bg: 'bg-rose-50',   border: 'border-rose-200',   text: 'text-rose-700',   bar: 'bg-rose-400'   },
  indigo: { bg: 'bg-indigo-50', border: 'border-indigo-200', text: 'text-indigo-700', bar: 'bg-indigo-400' },
};

const FACTOR_ICONS = {
  'RSI': '📊', 'MACD': '📈', 'EMA Trend': '〰️', 'Index ATR': '⚡',
  'OI Buildup': '🏗️', 'Volume': '📦', 'Order Flow': '🌊', 'Moneyness': '🎯',
  'Pivot Proximity': '📍', 'Time Window': '⏱️', 'PCR': '🔄', 'India VIX': '🌡️', 'LLM Analysis': '🤖',
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

const pctColor = (pts, max) => {
  const r = max > 0 ? pts / max : 0;
  return r >= 0.7 ? 'text-emerald-600' : r >= 0.4 ? 'text-yellow-600' : 'text-red-500';
};

const MiniBar = ({ pts, max, className = '' }) => {
  const pct = max > 0 ? Math.min(100, (pts / max) * 100) : 0;
  const color = pct >= 70 ? 'bg-emerald-400' : pct >= 40 ? 'bg-yellow-400' : 'bg-red-300';
  return (
    <div className={`flex items-center gap-1.5 ${className}`}>
      <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-xs font-mono w-8 text-right ${pctColor(pts, max)}`}>{pts}/{max}</span>
    </div>
  );
};

// ─── Expanded Row Content ──────────────────────────────────────────────────────

const ExpandedRow = ({ item, onAIAnalyse, aiLoading, aiError }) => {
  const navigate = useNavigate();

  const goMock = () => {
    const p = new URLSearchParams({ instrument: item.instrument });
    if (item.score != null) p.set('score', item.score);
    if (item.grade)          p.set('grade', item.grade);
    navigate(`/mock-trading?${p}`);
  };
  const goReal = () => navigate(`/orders?${new URLSearchParams({ instrument: item.instrument })}`);

  const llmFactor = item.factors?.['LLM Analysis'];
  const llmPts    = llmFactor?.pts ?? 0;
  const llmMax    = llmFactor?.max ?? 5;
  const hasLLM    = item.llm_reasoning?.length > 0;
  const llmSentiment =
    llmPts / llmMax >= 0.8 ? { label: 'BUY',       cls: 'bg-emerald-100 text-emerald-700 border-emerald-200' } :
    llmPts / llmMax >= 0.5 ? { label: 'LEAN BUY',  cls: 'bg-yellow-100 text-yellow-700 border-yellow-200'   } :
                             { label: 'HOLD/SKIP',  cls: 'bg-gray-100 text-gray-500 border-gray-200'         };

  return (
    <div className="px-4 py-4 bg-gray-50 border-t border-gray-100 space-y-4">

      {/* ── Row 1: Entry / SL / Target + Order Flow ─── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

        {/* Price targets */}
        {item.last_price != null && (
          <div>
            <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">Trade Setup</p>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="bg-white rounded-lg py-2 border border-gray-200">
                <p className="text-xs text-gray-400">Entry</p>
                <p className="text-sm font-bold text-gray-800">₹{item.suggested_entry?.toFixed(2)}</p>
              </div>
              <div className="bg-white rounded-lg py-2 border border-red-100">
                <p className="text-xs text-red-400">Stop-Loss</p>
                <p className="text-sm font-bold text-red-600">₹{item.suggested_sl?.toFixed(2)}</p>
              </div>
              <div className="bg-white rounded-lg py-2 border border-green-100">
                <p className="text-xs text-green-500">Target</p>
                <p className="text-sm font-bold text-green-700">₹{item.suggested_target?.toFixed(2)}</p>
              </div>
            </div>
          </div>
        )}

        {/* Order flow */}
        {item.order_flow?.imbalance != null && (() => {
          const { imbalance, total_buy_qty, total_sell_qty, best_bid, best_ask, spread_pct } = item.order_flow;
          const buyPct  = Math.round(imbalance * 100);
          const sellPct = 100 - buyPct;
          const dom =
            imbalance >= 0.65 ? { label: 'Buyer dominated', color: 'text-emerald-700' } :
            imbalance >= 0.55 ? { label: 'Slight buyer edge', color: 'text-green-600'  } :
            imbalance >= 0.45 ? { label: 'Balanced',          color: 'text-yellow-600' } :
            imbalance >= 0.35 ? { label: 'Slight seller edge','color': 'text-orange-600'} :
                                { label: 'Seller dominated',  color: 'text-red-600'    };
          return (
            <div>
              <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">🌊 Live Order Flow</p>
              <div className="bg-white rounded-lg border border-gray-200 px-3 py-2">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-gray-500">Bid/Ask depth imbalance</span>
                  <span className={`text-xs font-bold ${dom.color}`}>{dom.label}</span>
                </div>
                <div className="flex h-2.5 rounded-full overflow-hidden gap-0.5">
                  <div className="bg-emerald-400 rounded-l-full" style={{ width: `${buyPct}%` }} />
                  <div className="bg-red-400 rounded-r-full"    style={{ width: `${sellPct}%` }} />
                </div>
                <div className="flex justify-between mt-1 text-xs text-gray-500">
                  <span className="text-emerald-600 font-medium">Buy {buyPct}% ({total_buy_qty?.toLocaleString('en-IN') ?? '—'})</span>
                  {spread_pct != null && (
                    <span className={spread_pct > 1.0 ? 'text-red-500 font-medium' : 'text-gray-400'}>
                      Spread {spread_pct.toFixed(2)}%{spread_pct > 1.0 ? ' ⚠️' : ''}
                    </span>
                  )}
                  <span className="text-red-500 font-medium">Sell {sellPct}% ({total_sell_qty?.toLocaleString('en-IN') ?? '—'})</span>
                </div>
                {(best_bid != null || best_ask != null) && (
                  <div className="flex justify-between mt-0.5 text-xs text-gray-400">
                    <span>Bid ₹{best_bid?.toFixed(2) ?? '—'}</span>
                    <span>Ask ₹{best_ask?.toFixed(2) ?? '—'}</span>
                  </div>
                )}
              </div>
            </div>
          );
        })()}
      </div>

      {/* ── Row 2: Group scores + Factor breakdown ─── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

        {/* Group diagnosis */}
        {item.group_diagnosis && (
          <div>
            <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">🧮 Score by Group</p>
            <div className="space-y-1.5">
              {Object.entries(item.group_diagnosis).map(([key, { pts, max, note }]) => {
                const meta = GROUP_META[key] || { label: key, icon: '•', color: 'blue' };
                const cls  = COLOR_CLASSES[meta.color] || COLOR_CLASSES.blue;
                const pct  = max > 0 ? pts / max : 0;
                return (
                  <div key={key} className={`rounded-md border ${cls.border} ${cls.bg} px-2 py-1.5`}>
                    <div className="flex items-center justify-between mb-0.5">
                      <span className={`text-xs font-semibold ${cls.text}`}>{meta.icon} {meta.label}</span>
                      <span className={`text-xs font-mono font-bold ${pct >= 0.6 ? 'text-emerald-700' : pct >= 0.3 ? 'text-amber-700' : 'text-red-600'}`}>
                        {pts}/{max}
                      </span>
                    </div>
                    <div className="h-1 bg-white rounded-full overflow-hidden">
                      <div className={`h-full ${cls.bar}`} style={{ width: `${Math.min(100, pct * 100)}%` }} />
                    </div>
                    {note && <p className="text-xs text-gray-500 mt-0.5 italic leading-tight">{note}</p>}
                  </div>
                );
              })}
            </div>
            {item.veto_notes?.length > 0 && (
              <div className="mt-2 space-y-1">
                {item.veto_notes.map((n, i) => (
                  <div key={i} className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1">⚡ {n}</div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* All factors */}
        {item.factors && Object.keys(item.factors).length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">📋 All Factors</p>
            <div className="space-y-1.5">
              {Object.entries(item.factors).map(([name, { pts, max, reason }]) => (
                <div key={name}>
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="text-xs text-gray-600">{FACTOR_ICONS[name] || '•'} {name}</span>
                    <span className={`text-xs font-mono ${pctColor(pts, max)}`}>{pts}/{max}</span>
                  </div>
                  <MiniBar pts={pts} max={max} />
                  <p className="text-xs text-gray-400 leading-snug mt-0.5">{reason}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Row 3: AI Analysis ─────────────────────── */}
      <div className="border-t border-gray-200 pt-3">
        <div className="flex items-center justify-between mb-2">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">🤖 AI Multi-Timeframe Analysis</p>
          {hasLLM && (
            <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${llmSentiment.cls}`}>
              {llmSentiment.label} · {llmPts}/{llmMax} pts
            </span>
          )}
        </div>

        {hasLLM ? (
          <ul className="space-y-1">
            {item.llm_reasoning.map((line, i) => (
              <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                <span className="text-blue-400 mt-0.5 shrink-0">›</span>
                <span className="leading-snug">{line}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="flex items-center gap-3">
            <p className="text-xs text-gray-400 flex-1">
              AI analysis uses Claude to check weekly → daily → hourly alignment for this instrument.
              Each click costs ~1 Anthropic API credit.
            </p>
            <button
              onClick={() => onAIAnalyse(item)}
              disabled={aiLoading}
              className="shrink-0 flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold
                         bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300
                         text-white transition-colors"
            >
              {aiLoading
                ? <><span className="animate-spin inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full" /> Analysing…</>
                : '🤖 Analyse with AI'}
            </button>
          </div>
        )}
        {aiError && (
          <p className="mt-1.5 text-xs text-red-500">✗ {aiError}</p>
        )}
      </div>

      {/* ── Row 4: Trade actions ───────────────────── */}
      <div className="border-t border-gray-200 pt-3 flex gap-2">
        <button
          onClick={goMock}
          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg
                     bg-indigo-50 hover:bg-indigo-100 border border-indigo-200
                     text-indigo-700 text-xs font-semibold transition-colors"
        >
          🧪 Mock Buy
        </button>
        <button
          onClick={goReal}
          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg
                     bg-green-50 hover:bg-green-100 border border-green-200
                     text-green-700 text-xs font-semibold transition-colors"
        >
          💰 Real Buy{item.last_price ? ` @ ₹${item.last_price.toFixed(2)}` : ''}
        </button>
      </div>
    </div>
  );
};

// ─── Table Row ─────────────────────────────────────────────────────────────────

const TableRow = ({ item, isExpanded, onToggle, onAIAnalyse, aiLoading, aiError }) => {
  const style  = GRADE_STYLES[item.grade] || GRADE_STYLES['N/A'];
  const rsi    = item.factors?.['RSI'];
  const macd   = item.factors?.['MACD'];
  const flow   = item.order_flow;
  const flowPct = flow?.imbalance != null ? Math.round(flow.imbalance * 100) : null;

  // Momentum signal: combine RSI + MACD into one cell
  const momentumPts = (rsi?.pts ?? 0) + (macd?.pts ?? 0);
  const momentumMax = (rsi?.max ?? 12) + (macd?.max ?? 18);

  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer border-b border-gray-100 transition-colors
          ${isExpanded ? 'bg-indigo-50/50' : 'bg-white hover:bg-gray-50/60'}`}
      >
        {/* # */}
        <td className="px-3 py-3 text-xs text-gray-400 font-mono w-8 text-center">
          {item.rank}
        </td>

        {/* Symbol + moneyness */}
        <td className="px-3 py-3 min-w-0">
          <p className="font-mono font-semibold text-gray-900 text-xs truncate">{item.instrument}</p>
          <p className="text-xs text-gray-400 mt-0.5">
            {item.expiry ? new Date(item.expiry).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) : '—'}
            {item.lot_size ? ` · lot ${item.lot_size}` : ''}
          </p>
        </td>

        {/* Strike + OTM */}
        <td className="px-3 py-3 text-right">
          <p className="text-xs font-semibold text-gray-800">₹{item.strike?.toLocaleString('en-IN') ?? '—'}</p>
          {item.moneyness_pct != null && (
            <p className={`text-xs ${item.moneyness_pct > 0 ? 'text-orange-500' : 'text-blue-500'}`}>
              {item.moneyness_pct > 0 ? `+${item.moneyness_pct.toFixed(1)}%` : `${item.moneyness_pct.toFixed(1)}%`}
              {item.moneyness_pct > 0 ? ' OTM' : ' ITM'}
            </p>
          )}
        </td>

        {/* LTP */}
        <td className="px-3 py-3 text-right">
          <p className="text-xs font-semibold text-gray-800">
            {item.last_price != null ? `₹${item.last_price.toFixed(2)}` : '—'}
          </p>
        </td>

        {/* Score bar */}
        <td className="px-3 py-3 w-32">
          {item.score != null ? (
            <>
              <div className="flex items-center gap-1.5 mb-0.5">
                <span className="text-xs font-bold text-gray-700">{item.score}</span>
                <span className="text-xs text-gray-400">/100</span>
              </div>
              <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${style.bar}`} style={{ width: `${item.score}%` }} />
              </div>
            </>
          ) : (
            <span className="text-xs text-gray-400">—</span>
          )}
        </td>

        {/* Grade */}
        <td className="px-3 py-3 text-center">
          <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-extrabold ${style.text} ${style.bg} border ${style.border}`}>
            {item.grade}
          </span>
        </td>

        {/* Momentum (RSI+MACD combined) */}
        <td className="px-3 py-3 w-28 hidden md:table-cell">
          {rsi || macd ? (
            <MiniBar pts={momentumPts} max={momentumMax} />
          ) : <span className="text-xs text-gray-300">—</span>}
        </td>

        {/* Order Flow */}
        <td className="px-3 py-3 text-center hidden lg:table-cell">
          {flowPct != null ? (
            <div className="flex items-center gap-1 justify-center">
              <span className={`text-xs font-semibold ${
                flowPct >= 60 ? 'text-emerald-600' : flowPct >= 50 ? 'text-green-500' :
                flowPct >= 40 ? 'text-yellow-600' : 'text-red-500'
              }`}>{flowPct}%</span>
              <span className="text-xs text-gray-400">buy</span>
            </div>
          ) : <span className="text-xs text-gray-300">—</span>}
        </td>

        {/* Pivot */}
        <td className="px-3 py-3 text-center hidden lg:table-cell">
          {item.factors?.['Pivot Proximity'] ? (
            <span className={`text-xs font-medium ${pctColor(item.factors['Pivot Proximity'].pts, item.factors['Pivot Proximity'].max)}`}>
              {item.factors['Pivot Proximity'].pts}/{item.factors['Pivot Proximity'].max}
            </span>
          ) : <span className="text-xs text-gray-300">—</span>}
        </td>

        {/* AI status */}
        <td className="px-3 py-3 text-center hidden xl:table-cell">
          {item.llm_reasoning?.length > 0 ? (
            <span className="text-xs px-1.5 py-0.5 bg-indigo-100 text-indigo-700 rounded font-medium">✓ Done</span>
          ) : (
            <span className="text-xs text-gray-300">—</span>
          )}
        </td>

        {/* Expand chevron */}
        <td className="px-3 py-3 text-center w-8">
          <span className={`text-gray-400 text-xs transition-transform inline-block ${isExpanded ? 'rotate-180' : ''}`}>▼</span>
        </td>
      </tr>

      {/* Expanded detail row */}
      {isExpanded && (
        <tr>
          <td colSpan={11} className="p-0">
            <ExpandedRow
              item={item}
              onAIAnalyse={onAIAnalyse}
              aiLoading={aiLoading}
              aiError={aiError}
            />
          </td>
        </tr>
      )}
    </>
  );
};

// ─── Main page ────────────────────────────────────────────────────────────────

const WinProbability = () => {
  const [underlying, setUnderlying] = useState('NIFTY');
  const [maxResults, setMaxResults] = useState(20);
  const [loading, setLoading]       = useState(false);
  const [data, setData]             = useState(null);
  const [ranked, setRanked]         = useState([]);   // mutable copy so AI results can be merged in
  const [error, setError]           = useState('');
  const [tokenExpired, setTokenExpired]   = useState(false);
  const [noMarketData, setNoMarketData]   = useState(false);
  const [lastFetch, setLastFetch]   = useState(null);
  const [expandedRows, setExpandedRows]   = useState(new Set());
  const [aiLoading, setAiLoading]   = useState(null);  // tradingsymbol currently being analysed
  const [aiErrors, setAiErrors]     = useState({});    // { tradingsymbol: errorMsg }

  const fetchRanking = useCallback(async () => {
    setLoading(true);
    setError('');
    setTokenExpired(false);
    setNoMarketData(false);
    setExpandedRows(new Set());
    setAiLoading(null);
    setAiErrors({});
    try {
      const res = await rankAPI.getRanking(underlying, maxResults);
      setData(res.data);
      setRanked(res.data.ranked || []);
      setLastFetch(new Date());
    } catch (err) {
      const detail = err.response?.data?.detail || err.message || 'Failed to fetch ranking';
      if (err.response?.status === 403 && detail.includes('KITE_TOKEN_EXPIRED')) {
        setTokenExpired(true);
      } else if (err.response?.status === 403 && detail.includes('KITE_NO_MARKET_DATA')) {
        setNoMarketData(true);
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
      if (res.data?.login_url) window.location.href = res.data.login_url;
    } catch {
      window.location.href = '/dashboard';
    }
  };

  const toggleRow = (symbol) => {
    setExpandedRows(prev => {
      const next = new Set(prev);
      next.has(symbol) ? next.delete(symbol) : next.add(symbol);
      return next;
    });
  };

  // Called when user clicks "Analyse with AI" for a specific row
  const handleAIAnalyse = useCallback(async (item) => {
    setAiLoading(item.instrument);
    setAiErrors(prev => ({ ...prev, [item.instrument]: null }));
    try {
      const res = await rankAPI.analyzeLLM({
        tradingsymbol:    item.instrument,
        instrument_token: item.instrument_token || 0,
        strike:           item.strike,
        expiry:           item.expiry,
        lot_size:         item.lot_size,
        underlying:       data?.underlying || underlying,
        spot_price:       item.spot_price,
        vix:              data?.india_vix,
        pcr:              data?.pcr,
      });
      // Merge the LLM result back into ranked list
      setRanked(prev => prev.map(r =>
        r.instrument === item.instrument
          ? { ...r, llm_reasoning: res.data.llm_reasoning, factors: res.data.factors }
          : r
      ));
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'AI analysis failed';
      setAiErrors(prev => ({ ...prev, [item.instrument]: msg }));
    } finally {
      setAiLoading(null);
    }
  }, [data, underlying]);

  const gradeCount = (grade) => ranked.filter(i => i.grade === grade).length;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="mb-5">
        <h1 className="text-2xl font-bold text-gray-900">Win Probability Ranker</h1>
        <p className="mt-1 text-sm text-gray-500">
          Ranks call options by intraday profit probability across 13 factors — RSI, MACD, EMA &amp; ATR on{' '}
          <span className="font-medium text-blue-600">underlying index</span>, live order flow, PCR, India VIX, and Pivot/S1 proximity.
          <span className="ml-1 text-indigo-600 font-medium">AI analysis is on-demand per row</span> to save API costs.
        </p>
      </div>

      {/* ── Controls ───────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 mb-5">
        <div className="flex flex-wrap items-end gap-3">

          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Underlying</label>
            <div className="flex gap-1.5">
              {UNDERLYINGS.map(u => (
                <button key={u} onClick={() => setUnderlying(u)}
                  className={`px-3 py-1.5 rounded-md text-xs font-bold border transition-colors ${
                    underlying === u ? 'bg-blue-600 text-white border-blue-600'
                                     : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400 hover:text-blue-600'
                  }`}
                >{u}</button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Results</label>
            <select value={maxResults} onChange={e => setMaxResults(Number(e.target.value))}
              className="border border-gray-300 rounded-md px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {[10, 20, 30, 40].map(n => <option key={n} value={n}>Top {n}</option>)}
            </select>
          </div>

          <button onClick={fetchRanking} disabled={loading}
            className="ml-auto px-5 py-2 rounded-lg font-semibold text-sm bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white flex items-center gap-2 transition-colors"
          >
            {loading
              ? <><span className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" /> Scanning…</>
              : '🔍 Rank Instruments'}
          </button>
        </div>

        {/* Grade legend */}
        <div className="mt-3 pt-3 border-t border-gray-100 flex flex-wrap gap-x-5 gap-y-1">
          {[
            ['A+', 'Very High (>80%)'], ['A', 'High (65–80%)'], ['B', 'Moderate (50–65%)'],
            ['C', 'Low (35–50%)'],      ['D', 'Very Low (<35%)'],
          ].map(([g, label]) => {
            const s = GRADE_STYLES[g];
            return (
              <div key={g} className="flex items-center gap-1.5 text-xs text-gray-500">
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold ${s.bg} ${s.text} border ${s.border}`}>{g}</span>
                {label}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Banners ─────────────────────────────────────────────────────── */}
      {error && (
        <div className="mb-4 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">✗ {error}</div>
      )}

      {tokenExpired && (
        <div className="mb-4 px-5 py-4 rounded-xl bg-amber-50 border border-amber-300 flex items-start gap-4">
          <span className="text-2xl shrink-0">🔑</span>
          <div className="flex-1">
            <p className="text-sm font-bold text-amber-800">Kite Session Expired</p>
            <p className="text-sm text-amber-700 mt-0.5">Your Kite access token has expired (resets daily). Re-link to restore live data.</p>
            <button onClick={handleRelinkKite}
              className="mt-3 px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold">
              🔗 Re-link Kite Account
            </button>
          </div>
        </div>
      )}

      {noMarketData && (
        <div className="mb-4 px-5 py-4 rounded-xl bg-orange-50 border-2 border-orange-300 flex items-start gap-4">
          <span className="text-2xl shrink-0">⚠️</span>
          <div className="flex-1">
            <p className="text-sm font-bold text-orange-800">Kite Connect Personal Plan — No Market Data</p>
            <p className="text-sm text-orange-700 mt-1">
              Free Personal plan has no market data. Create a{' '}
              <a href="https://developers.kite.trade" target="_blank" rel="noopener noreferrer" className="underline font-semibold">
                paid Kite Connect app
              </a>{' '}
              (₹500/month — historical data included free).
            </p>
          </div>
        </div>
      )}

      {/* ── Empty state ──────────────────────────────────────────────────── */}
      {!loading && !data && !error && !tokenExpired && !noMarketData && (
        <div className="text-center py-20 text-gray-400">
          <div className="text-5xl mb-4">🎯</div>
          <p className="text-lg font-medium">Select an underlying and click "Rank Instruments"</p>
          <p className="text-sm mt-1">Click any row to expand — use "Analyse with AI" for on-demand LLM analysis per instrument.</p>
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────────── */}
      {data && (
        <>
          {/* Degraded mode banner */}
          {!data.market_data_available && (
            <div className="mb-4 px-5 py-3 rounded-xl bg-orange-50 border-2 border-orange-300 flex items-center gap-3 text-sm">
              <span className="text-xl">⚠️</span>
              <div>
                <span className="font-bold text-orange-800">Live Scores Unavailable — Instrument List Only. </span>
                <span className="text-orange-700">
                  Free Personal plan — no market data. Upgrade to{' '}
                  <a href="https://developers.kite.trade" target="_blank" rel="noopener noreferrer" className="underline font-semibold">
                    paid Connect app
                  </a>{' '}
                  (₹500/month, historical data free).
                </span>
              </div>
            </div>
          )}

          {/* Summary strip */}
          <div className="bg-white rounded-xl border border-gray-100 px-4 py-2.5 mb-3 flex flex-wrap items-center gap-4 text-sm shadow-sm">
            <span className="font-bold text-gray-800">{data.underlying}</span>
            {data.spot_price && (
              <span className="text-gray-600">
                Spot <strong className="text-gray-900">₹{data.spot_price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</strong>
              </span>
            )}
            {data.india_vix != null && (
              <span className={`font-semibold ${data.india_vix > 18 ? 'text-red-600' : data.india_vix >= 10 ? 'text-emerald-600' : 'text-yellow-600'}`}>
                VIX {data.india_vix.toFixed(1)}{data.india_vix > 18 ? ' ⚠️' : ' ✓'}
              </span>
            )}
            {data.pcr != null && (
              <span className={`font-semibold ${data.pcr >= 1.2 ? 'text-emerald-600' : data.pcr < 0.7 ? 'text-red-600' : 'text-gray-700'}`}>
                PCR {data.pcr.toFixed(2)}{data.pcr >= 1.2 ? ' (bullish ✓)' : data.pcr < 0.7 ? ' (bearish ⚠️)' : ''}
              </span>
            )}
            <span className="text-gray-500">{data.scanned} scanned</span>
            {data.market_data_available && data.scan_time_ms > 0 && (
              <span className="text-gray-400 text-xs">{(data.scan_time_ms / 1000).toFixed(1)}s</span>
            )}
            {/* Grade pills */}
            {data.market_data_available && (
              <div className="ml-auto flex gap-1 flex-wrap">
                {['A+', 'A', 'B', 'C', 'D'].map(g => {
                  const cnt = gradeCount(g);
                  if (!cnt) return null;
                  const s = GRADE_STYLES[g];
                  return (
                    <span key={g} className={`px-1.5 py-0.5 rounded-full text-xs font-bold ${s.bg} ${s.text} border ${s.border}`}>
                      {g}×{cnt}
                    </span>
                  );
                })}
              </div>
            )}
            {lastFetch && <p className="text-xs text-gray-400 w-full -mt-1">Last updated {lastFetch.toLocaleTimeString('en-IN')}</p>}
          </div>

          {/* ── Main table ── */}
          {ranked.length === 0 ? (
            <div className="text-center py-16 text-gray-400">
              <p>No instruments found for {data.underlying}.</p>
            </div>
          ) : (
            <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-3 py-2.5 text-center text-xs font-semibold text-gray-500 uppercase tracking-wide w-8">#</th>
                    <th className="px-3 py-2.5 text-left   text-xs font-semibold text-gray-500 uppercase tracking-wide">Symbol</th>
                    <th className="px-3 py-2.5 text-right  text-xs font-semibold text-gray-500 uppercase tracking-wide">Strike / OTM</th>
                    <th className="px-3 py-2.5 text-right  text-xs font-semibold text-gray-500 uppercase tracking-wide">LTP</th>
                    <th className="px-3 py-2.5 text-left   text-xs font-semibold text-gray-500 uppercase tracking-wide w-32">Score</th>
                    <th className="px-3 py-2.5 text-center text-xs font-semibold text-gray-500 uppercase tracking-wide">Grade</th>
                    <th className="px-3 py-2.5 text-left   text-xs font-semibold text-gray-500 uppercase tracking-wide hidden md:table-cell w-28">Momentum</th>
                    <th className="px-3 py-2.5 text-center text-xs font-semibold text-gray-500 uppercase tracking-wide hidden lg:table-cell">Flow</th>
                    <th className="px-3 py-2.5 text-center text-xs font-semibold text-gray-500 uppercase tracking-wide hidden lg:table-cell">Pivot</th>
                    <th className="px-3 py-2.5 text-center text-xs font-semibold text-gray-500 uppercase tracking-wide hidden xl:table-cell">AI</th>
                    <th className="px-3 py-2.5 w-8"></th>
                  </tr>
                </thead>
                <tbody>
                  {ranked.map(item => (
                    <TableRow
                      key={item.instrument}
                      item={item}
                      isExpanded={expandedRows.has(item.instrument)}
                      onToggle={() => toggleRow(item.instrument)}
                      onAIAnalyse={handleAIAnalyse}
                      aiLoading={aiLoading === item.instrument}
                      aiError={aiErrors[item.instrument]}
                    />
                  ))}
                </tbody>
              </table>
              <p className="text-xs text-gray-400 px-4 py-2 border-t border-gray-100">
                Click any row to expand details · AI analysis is on-demand per row (saves API credits)
              </p>
            </div>
          )}

          {/* Disclaimer */}
          <div className="mt-4 px-4 py-3 rounded-lg bg-gray-50 border border-gray-200 text-xs text-gray-500 leading-relaxed">
            {data.market_data_available ? (
              <><strong>Disclaimer:</strong> Scores use 13 factors including index RSI/MACD/EMA/ATR, live order book, PCR, VIX, and Pivot levels.
              Not financial advice. Options trading carries significant risk. Use your own judgment.</>
            ) : (
              <><strong>Limited mode:</strong> Showing static instrument data (strike/expiry/lot). Live prices and scores require a paid Kite Connect app (₹500/month at developers.kite.trade).</>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default WinProbability;
