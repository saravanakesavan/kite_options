import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { mockAPI, tradingAPI } from '../services/api';

// ─── Constants ────────────────────────────────────────────────────────────────
const MAX_SLOTS = 5;
const POLL_OPTIONS_SEC = [10, 15, 30, 60, 120];

// Default SL and target levels used for visual progress bars
const DEFAULT_SL_PCT     = -3;   // −3%
const DEFAULT_TARGET_PCT = +8;   // +8%

// ─── Helper: format ₹ amounts ─────────────────────────────────────────────────
const fmt = (n, dec = 2) =>
  n != null ? `₹${Number(n).toFixed(dec)}` : '—';

// ─── PnL badge ────────────────────────────────────────────────────────────────
function PnLBadge({ pnl, pnlPct }) {
  if (pnl == null) return <span className="text-gray-400 text-sm">Fetching…</span>;
  const positive = pnl >= 0;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-sm font-semibold ${
        positive ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
      }`}
    >
      {positive ? '▲' : '▼'} {fmt(pnl)}{' '}
      {pnlPct != null && (
        <span className="font-normal opacity-80">({pnlPct > 0 ? '+' : ''}{pnlPct.toFixed(2)}%)</span>
      )}
    </span>
  );
}

// ─── Position Age Chip ────────────────────────────────────────────────────────
/**
 * Shows how long this mock position has been open.
 * Options lose value via theta — knowing age helps the user decide whether
 * to hold or cut a position that's not moving.
 */
function PositionAgeChip({ entryTime }) {
  const [label, setLabel] = React.useState('');

  React.useEffect(() => {
    const compute = () => {
      const diffMs = Date.now() - new Date(entryTime).getTime();
      const totalMin = Math.floor(diffMs / 60000);
      if (totalMin < 1)       setLabel('< 1 min');
      else if (totalMin < 60) setLabel(`${totalMin}m`);
      else {
        const h = Math.floor(totalMin / 60);
        const m = totalMin % 60;
        setLabel(`${h}h ${m}m`);
      }
    };
    compute();
    const t = setInterval(compute, 30000); // refresh every 30 s
    return () => clearInterval(t);
  }, [entryTime]);

  const diffMin = Math.floor((Date.now() - new Date(entryTime).getTime()) / 60000);
  // Warn in amber after 90 min (heavy theta decay zone)
  const cls = diffMin > 90
    ? 'bg-amber-50 text-amber-700 border-amber-200'
    : 'bg-gray-50 text-gray-500 border-gray-200';

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border ${cls}`}
          title={`Position opened at ${new Date(entryTime).toLocaleTimeString('en-IN', { hour12: true })}`}>
      ⏱ {label}
      {diffMin > 90 && <span className="font-semibold">· theta ⚠️</span>}
    </span>
  );
}

// ─── Exit Level Bar ────────────────────────────────────────────────────────────
/**
 * Visual progress bar that shows how close the current P&L% is to the
 * suggested SL (−3%) and Target (+8%) levels.
 *
 * Zone:  [SL −3%] -------- [0%] -------- [Target +8%]
 * The needle moves left on loss, right on profit.
 */
function ExitLevelBar({ pnlPct }) {
  if (pnlPct == null) return null;

  const SL     = DEFAULT_SL_PCT;     // −3
  const TARGET = DEFAULT_TARGET_PCT; // +8
  const RANGE  = TARGET - SL;        // 11

  // Clamp needle to [SL−2, TARGET+2] so it doesn't fly off the bar
  const clamped = Math.max(SL - 2, Math.min(TARGET + 2, pnlPct));
  // Map to 0–100% width
  const pct = ((clamped - (SL - 2)) / (RANGE + 4)) * 100;

  // SL marker position on bar (%)
  const slPos     = ((SL - (SL - 2)) / (RANGE + 4)) * 100;     // ≈ 18 %
  // Target marker position on bar (%)
  const targetPos = ((TARGET - (SL - 2)) / (RANGE + 4)) * 100; // ≈ 91 %

  const atSL     = pnlPct <= SL;
  const atTarget = pnlPct >= TARGET;

  return (
    <div className="mt-3">
      <div className="flex justify-between text-xs mb-1">
        <span className={`font-semibold ${atSL ? 'text-red-600 animate-pulse' : 'text-red-400'}`}>
          {atSL ? '🔴 SL HIT' : `SL ${SL}%`}
        </span>
        <span className={`font-mono text-xs ${pnlPct >= 0 ? 'text-green-600' : 'text-red-500'}`}>
          {pnlPct > 0 ? '+' : ''}{pnlPct.toFixed(2)}%
        </span>
        <span className={`font-semibold ${atTarget ? 'text-green-600 animate-pulse' : 'text-green-500'}`}>
          {atTarget ? '🎯 TARGET HIT' : `Target +${TARGET}%`}
        </span>
      </div>

      {/* Track */}
      <div className="relative h-3 rounded-full overflow-visible"
           style={{ background: `linear-gradient(to right, #fecaca 0%, #fecaca ${slPos}%, #f0fdf4 ${slPos}%, #f0fdf4 ${targetPos}%, #bbf7d0 ${targetPos}%, #bbf7d0 100%)` }}>

        {/* SL marker line */}
        <div className="absolute top-0 bottom-0 w-0.5 bg-red-400 opacity-70"
             style={{ left: `${slPos}%` }} />

        {/* Target marker line */}
        <div className="absolute top-0 bottom-0 w-0.5 bg-green-500 opacity-70"
             style={{ left: `${targetPos}%` }} />

        {/* Needle */}
        <div
          className={`absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full border-2 border-white shadow-md transition-all duration-500 ${
            atSL ? 'bg-red-500' : atTarget ? 'bg-green-500' : pnlPct >= 0 ? 'bg-blue-500' : 'bg-orange-400'
          }`}
          style={{ left: `calc(${pct}% - 6px)` }}
        />
      </div>

      {(atSL || atTarget) && (
        <p className={`text-xs font-bold mt-1 text-center ${atSL ? 'text-red-600' : 'text-green-600'}`}>
          {atSL ? '⚠️ Consider closing — stop-loss level reached!' : '🎯 Target reached — consider booking profit!'}
        </p>
      )}
    </div>
  );
}

// ─── Position Card ────────────────────────────────────────────────────────────
function PositionCard({ pos, onClose, closing }) {
  const positive = pos.pnl != null && pos.pnl >= 0;

  return (
    <div
      className={`rounded-xl border-2 p-4 shadow-sm bg-white transition-all ${
        pos.pnl == null
          ? 'border-gray-200'
          : positive
          ? 'border-green-300'
          : 'border-red-300'
      }`}
    >
      {/* Header row */}
      <div className="flex justify-between items-start mb-2">
        <div>
          <p className="font-bold text-gray-800 text-base leading-tight">
            {pos.instrument}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">
            Qty: {pos.quantity} · Entry: {fmt(pos.entry_price)}
          </p>
          {pos.notes && (
            <p className="text-xs italic text-gray-500 mt-0.5">"{pos.notes}"</p>
          )}
        </div>

        <button
          onClick={() => onClose(pos.id)}
          disabled={closing}
          className="ml-2 px-3 py-1.5 bg-red-500 hover:bg-red-600 disabled:opacity-50 text-white text-xs font-medium rounded-lg transition-colors"
          title="Close this mock position"
        >
          {closing ? 'Closing…' : 'Close'}
        </button>
      </div>

      {/* Live stats row */}
      <div className="flex flex-wrap items-center gap-3 mt-3">
        <div className="text-sm text-gray-600">
          LTP:{' '}
          <span className="font-semibold text-gray-800">
            {pos.current_ltp != null ? fmt(pos.current_ltp) : '…'}
          </span>
        </div>
        <PnLBadge pnl={pos.pnl} pnlPct={pos.pnl_pct} />
        <PositionAgeChip entryTime={pos.entry_time} />
      </div>

      {/* SL / Target progress bar */}
      <ExitLevelBar pnlPct={pos.pnl_pct} />

      {/* Entry time */}
      <p className="text-xs text-gray-400 mt-2">
        Opened {new Date(pos.entry_time).toLocaleString('en-IN', { hour12: true })}
      </p>
    </div>
  );
}

// ─── Closed Trade Row ─────────────────────────────────────────────────────────
function ClosedTradeRow({ trade, onDelete, deleting }) {
  const pnl =
    trade.exit_price != null
      ? (trade.exit_price - trade.entry_price) * trade.quantity
      : null;

  return (
    <tr className="border-b border-gray-100 hover:bg-gray-50 text-sm">
      <td className="py-2 px-3 font-medium text-gray-700">{trade.instrument}</td>
      <td className="py-2 px-3 text-gray-600">{trade.quantity}</td>
      <td className="py-2 px-3 text-gray-600">{fmt(trade.entry_price)}</td>
      <td className="py-2 px-3 text-gray-600">
        {trade.exit_price != null ? fmt(trade.exit_price) : '—'}
      </td>
      <td className="py-2 px-3">
        {pnl != null ? (
          <span
            className={`font-semibold ${pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}
          >
            {pnl >= 0 ? '+' : ''}{fmt(pnl)}
          </span>
        ) : (
          '—'
        )}
      </td>
      <td className="py-2 px-3 text-gray-400 text-xs">
        {trade.exit_time
          ? new Date(trade.exit_time).toLocaleString('en-IN', { hour12: true })
          : '—'}
      </td>
      <td className="py-2 px-3">
        <button
          onClick={() => onDelete(trade.id)}
          disabled={deleting}
          className="text-red-400 hover:text-red-600 disabled:opacity-40 text-xs underline"
        >
          Delete
        </button>
      </td>
    </tr>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function MockTrading() {
  const location = useLocation();

  // Live positions from /mock/trades/pnl
  const [positions, setPositions]     = useState([]);
  const [totalPnl, setTotalPnl]       = useState(0);
  const [usedSlots, setUsedSlots]     = useState(0);
  const [lastUpdated, setLastUpdated] = useState(null);

  // Closed history
  const [closedTrades, setClosedTrades] = useState([]);
  const [showClosed, setShowClosed]     = useState(false);

  // Form state
  const [instrument, setInstrument]   = useState('');
  const [quantity, setQuantity]       = useState(1);
  const [notes, setNotes]             = useState('');
  const [submitting, setSubmitting]   = useState(false);
  // Score/grade passed from Win Probability page via query param
  const [wpScore, setWpScore] = useState(null);
  const [wpGrade, setWpGrade] = useState(null);

  // Instrument picker (live from /instruments)
  const [searchQuery, setSearchQuery]   = useState('');
  const [allInstruments, setAllInstruments] = useState([]);
  const [filteredList, setFilteredList] = useState([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [loadingInstr, setLoadingInstr] = useState(false);

  // Poll control
  const [pollInterval, setPollInterval] = useState(30); // seconds
  const [polling, setPolling]           = useState(true);
  const [loadingPnl, setLoadingPnl]     = useState(false);

  // Action states
  const [closingId, setClosingId]   = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const [error, setError]     = useState('');
  const [success, setSuccess] = useState('');

  const pollRef    = useRef(null);
  const dropdownRef = useRef(null);

  // ── Pre-fill from query params (deep-link from Win Probability) ─────────────
  // ?instrument=NIFTY25JUN24500CE&score=72.5&grade=A
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const sym   = params.get('instrument');
    const score = params.get('score');
    const grade = params.get('grade');
    if (sym) {
      const upper = sym.toUpperCase();
      setInstrument(upper);
      setSearchQuery(upper);
      // Scroll the buy form into view smoothly after render
      setTimeout(() => {
        document.getElementById('mock-buy-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 100);
    }
    if (score) setWpScore(parseFloat(score));
    if (grade) setWpGrade(grade);
  }, [location.search]);

  // ── Fetch live P&L ───────────────────────────────────────────────────────
  const fetchPnL = useCallback(async (silent = false) => {
    if (!silent) setLoadingPnl(true);
    try {
      const res = await mockAPI.getPnL();
      setPositions(res.data.positions || []);
      setTotalPnl(res.data.total_pnl || 0);
      setUsedSlots(res.data.used_slots || 0);
      setLastUpdated(new Date());
    } catch (e) {
      if (!silent) setError(e.response?.data?.detail || 'Failed to fetch live P&L');
    } finally {
      if (!silent) setLoadingPnl(false);
    }
  }, []);

  // ── Fetch closed trades ───────────────────────────────────────────────────
  const fetchClosed = useCallback(async () => {
    try {
      const res = await mockAPI.listTrades('CLOSED');
      setClosedTrades(res.data || []);
    } catch (_) {}
  }, []);

  // ── Load instruments for picker ───────────────────────────────────────────
  const loadInstruments = useCallback(async () => {
    if (allInstruments.length > 0) return;          // already loaded
    setLoadingInstr(true);
    try {
      const res = await tradingAPI.getInstruments();
      const list = (res.data.instruments || []).map(i => i.tradingsymbol).filter(Boolean);
      setAllInstruments(list);
    } catch (_) {
      // Silent — user can still type manually
    } finally {
      setLoadingInstr(false);
    }
  }, [allInstruments.length]);

  // ── Auto-complete filter ──────────────────────────────────────────────────
  useEffect(() => {
    if (!searchQuery) {
      setFilteredList([]);
      return;
    }
    const q = searchQuery.toUpperCase();
    setFilteredList(allInstruments.filter(s => s.includes(q)).slice(0, 15));
  }, [searchQuery, allInstruments]);

  // ── Click outside to close dropdown ──────────────────────────────────────
  useEffect(() => {
    const handler = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // ── Polling ───────────────────────────────────────────────────────────────
  useEffect(() => {
    fetchPnL();
    if (polling) {
      pollRef.current = setInterval(() => fetchPnL(true), pollInterval * 1000);
    }
    return () => clearInterval(pollRef.current);
  }, [pollInterval, polling, fetchPnL]);

  // ── Clear messages after 5 s ──────────────────────────────────────────────
  useEffect(() => {
    if (error || success) {
      const t = setTimeout(() => { setError(''); setSuccess(''); }, 5000);
      return () => clearTimeout(t);
    }
  }, [error, success]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleOpenTrade = async (e) => {
    e.preventDefault();
    if (!instrument.trim()) { setError('Please enter or select an instrument.'); return; }
    if (quantity < 1)        { setError('Quantity must be at least 1.'); return; }
    setSubmitting(true);
    setError('');
    try {
      await mockAPI.openTrade(
        instrument.trim().toUpperCase(),
        quantity,
        notes || null,
        wpScore,   // win probability score from Win Probability page (or null)
        wpGrade,   // grade from Win Probability page (or null)
      );
      setSuccess(`Mock BUY opened: ${instrument.toUpperCase()} × ${quantity}`);
      setInstrument('');
      setSearchQuery('');
      setQuantity(1);
      setNotes('');
      setWpScore(null);
      setWpGrade(null);
      fetchPnL();
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to open mock trade');
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = async (id) => {
    setClosingId(id);
    try {
      const res = await mockAPI.closeTrade(id);
      const d = res.data;
      setSuccess(
        `Closed ${d.instrument} — P&L: ${d.pnl != null ? (d.pnl >= 0 ? '+' : '') + '₹' + d.pnl.toFixed(2) : 'N/A'}`
      );
      fetchPnL();
      fetchClosed();
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to close mock trade');
    } finally {
      setClosingId(null);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this closed trade record permanently?')) return;
    setDeletingId(id);
    try {
      await mockAPI.deleteTrade(id);
      setClosedTrades(prev => prev.filter(t => t.id !== id));
      setSuccess('Trade record deleted.');
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to delete trade');
    } finally {
      setDeletingId(null);
    }
  };

  const handleToggleClosed = () => {
    if (!showClosed) fetchClosed();
    setShowClosed(v => !v);
  };

  // ── Render ────────────────────────────────────────────────────────────────
  const slotsLeft = MAX_SLOTS - usedSlots;

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">

      {/* Page header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">🧪 Mock Trading</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Paper trade with live prices. No real orders placed.
          </p>
        </div>

        {/* Slot indicator */}
        <div className="flex items-center gap-2">
          {[...Array(MAX_SLOTS)].map((_, i) => (
            <div
              key={i}
              className={`w-3 h-6 rounded-sm ${
                i < usedSlots ? 'bg-blue-500' : 'bg-gray-200'
              }`}
              title={i < usedSlots ? 'Slot in use' : 'Slot free'}
            />
          ))}
          <span className="text-xs text-gray-500 ml-1">
            {usedSlots}/{MAX_SLOTS} slots
          </span>
        </div>
      </div>

      {/* Notifications */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
          ⚠️ {error}
        </div>
      )}
      {success && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-lg text-sm">
          ✅ {success}
        </div>
      )}

      {/* ── Buy Form ──────────────────────────────────────────────────────── */}
      <div id="mock-buy-form" className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-gray-800">Open Mock Position</h2>
          {/* Badge shown when arriving from Win Probability page */}
          {wpScore != null && wpGrade && (
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-50 border border-indigo-200 text-xs">
              <span className="text-indigo-500">🎯 Win Probability</span>
              <span className="font-bold text-indigo-700">Grade {wpGrade}</span>
              <span className="text-indigo-500">·</span>
              <span className="font-mono font-bold text-indigo-700">{wpScore}/100</span>
              <button
                type="button"
                onClick={() => { setWpScore(null); setWpGrade(null); }}
                className="ml-1 text-indigo-400 hover:text-indigo-700 font-bold"
                title="Clear score"
              >×</button>
            </div>
          )}
        </div>

        <form onSubmit={handleOpenTrade} className="flex flex-wrap gap-3 items-end">

          {/* Instrument picker */}
          <div className="flex-1 min-w-[180px]" ref={dropdownRef}>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Instrument
            </label>
            <input
              type="text"
              value={searchQuery || instrument}
              placeholder="e.g. NIFTY25JUN24500CE"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              onChange={e => {
                setSearchQuery(e.target.value);
                setInstrument(e.target.value);
                setShowDropdown(true);
                if (allInstruments.length === 0) loadInstruments();
              }}
              onFocus={() => {
                setShowDropdown(true);
                if (allInstruments.length === 0) loadInstruments();
              }}
            />

            {/* Dropdown */}
            {showDropdown && (searchQuery || loadingInstr) && (
              <div className="absolute z-20 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-48 overflow-y-auto w-72">
                {loadingInstr ? (
                  <p className="px-3 py-2 text-xs text-gray-400">Loading instruments…</p>
                ) : filteredList.length === 0 ? (
                  <p className="px-3 py-2 text-xs text-gray-400">
                    No matches — type exact symbol and press Enter
                  </p>
                ) : (
                  filteredList.map(sym => (
                    <button
                      key={sym}
                      type="button"
                      className="w-full text-left px-3 py-1.5 text-sm hover:bg-blue-50 text-gray-700"
                      onMouseDown={() => {
                        setInstrument(sym);
                        setSearchQuery(sym);
                        setShowDropdown(false);
                      }}
                    >
                      {sym}
                    </button>
                  ))
                )}
              </div>
            )}
          </div>

          {/* Quantity */}
          <div className="w-24">
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Qty (lots)
            </label>
            <input
              type="number"
              min={1}
              max={50}
              value={quantity}
              onChange={e => setQuantity(parseInt(e.target.value) || 1)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>

          {/* Notes */}
          <div className="flex-1 min-w-[140px]">
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Note (optional)
            </label>
            <input
              type="text"
              value={notes}
              placeholder="e.g. RSI divergence"
              maxLength={100}
              onChange={e => setNotes(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={submitting || slotsLeft <= 0}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-medium text-sm rounded-lg transition-colors"
          >
            {submitting
              ? 'Opening…'
              : slotsLeft <= 0
              ? 'Slots Full'
              : '📥 Mock Buy'}
          </button>
        </form>

        {slotsLeft <= 0 && (
          <p className="mt-2 text-xs text-amber-600">
            All {MAX_SLOTS} mock slots are full. Close a position to add a new one.
          </p>
        )}
      </div>

      {/* ── Live Positions ─────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">

        {/* Section header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-3">
            <h2 className="font-semibold text-gray-800">Open Positions</h2>

            {/* Total P&L pill */}
            {positions.length > 0 && (
              <span
                className={`px-3 py-0.5 rounded-full text-sm font-bold ${
                  totalPnl >= 0
                    ? 'bg-green-100 text-green-700'
                    : 'bg-red-100 text-red-700'
                }`}
              >
                Total P&L: {totalPnl >= 0 ? '+' : ''}{fmt(totalPnl)}
              </span>
            )}
          </div>

          {/* Poll controls */}
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <label className="font-medium">Refresh every</label>
            <select
              value={pollInterval}
              onChange={e => {
                setPollInterval(Number(e.target.value));
              }}
              className="border border-gray-300 rounded-md px-2 py-1 text-xs focus:outline-none"
            >
              {POLL_OPTIONS_SEC.map(s => (
                <option key={s} value={s}>
                  {s}s
                </option>
              ))}
            </select>

            <button
              onClick={() => setPolling(v => !v)}
              className={`px-2 py-1 rounded text-xs font-medium ${
                polling
                  ? 'bg-green-100 text-green-700 hover:bg-green-200'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
              title={polling ? 'Pause auto-refresh' : 'Resume auto-refresh'}
            >
              {polling ? '⏸ Pause' : '▶ Resume'}
            </button>

            <button
              onClick={() => fetchPnL()}
              disabled={loadingPnl}
              className="px-2 py-1 rounded bg-blue-50 text-blue-600 hover:bg-blue-100 text-xs font-medium disabled:opacity-50"
            >
              {loadingPnl ? '…' : '↻ Now'}
            </button>

            {lastUpdated && (
              <span className="text-gray-400">
                {lastUpdated.toLocaleTimeString('en-IN', { hour12: true })}
              </span>
            )}
          </div>
        </div>

        {/* Position cards */}
        {positions.length === 0 ? (
          <div className="text-center py-10 text-gray-400">
            <p className="text-3xl mb-2">📋</p>
            <p className="text-sm">No open mock positions.</p>
            <p className="text-xs mt-1">Use the form above to open your first paper trade.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {positions.map(pos => (
              <PositionCard
                key={pos.id}
                pos={pos}
                onClose={handleClose}
                closing={closingId === pos.id}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Closed History ─────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <button
          onClick={handleToggleClosed}
          className="flex items-center gap-2 text-sm font-semibold text-gray-700 hover:text-blue-600 transition-colors"
        >
          <span>{showClosed ? '▼' : '▶'}</span>
          Closed Trades History
          {closedTrades.length > 0 && (
            <span className="ml-1 bg-gray-100 text-gray-600 text-xs px-2 py-0.5 rounded-full">
              {closedTrades.length}
            </span>
          )}
        </button>

        {showClosed && (
          <div className="mt-4 overflow-x-auto">
            {closedTrades.length === 0 ? (
              <p className="text-sm text-gray-400 py-4 text-center">No closed trades yet.</p>
            ) : (
              <table className="w-full text-left">
                <thead>
                  <tr className="text-xs uppercase text-gray-400 border-b border-gray-200">
                    <th className="py-2 px-3">Instrument</th>
                    <th className="py-2 px-3">Qty</th>
                    <th className="py-2 px-3">Entry</th>
                    <th className="py-2 px-3">Exit</th>
                    <th className="py-2 px-3">P&L</th>
                    <th className="py-2 px-3">Closed At</th>
                    <th className="py-2 px-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {closedTrades.map(trade => (
                    <ClosedTradeRow
                      key={trade.id}
                      trade={trade}
                      onDelete={handleDelete}
                      deleting={deletingId === trade.id}
                    />
                  ))}
                </tbody>

                {/* Summary row */}
                {closedTrades.length > 0 && (() => {
                  const totalClosed = closedTrades.reduce((acc, t) => {
                    if (t.exit_price != null && t.entry_price != null) {
                      return acc + (t.exit_price - t.entry_price) * t.quantity;
                    }
                    return acc;
                  }, 0);
                  return (
                    <tfoot>
                      <tr className="border-t-2 border-gray-200 text-sm font-semibold">
                        <td colSpan={4} className="py-2 px-3 text-gray-600">Total closed P&L</td>
                        <td className={`py-2 px-3 ${totalClosed >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          {totalClosed >= 0 ? '+' : ''}{fmt(totalClosed)}
                        </td>
                        <td colSpan={2} />
                      </tr>
                    </tfoot>
                  );
                })()}
              </table>
            )}
          </div>
        )}
      </div>

      {/* ── Info Footer ────────────────────────────────────────────────────── */}
      <div className="text-xs text-gray-400 bg-gray-50 rounded-lg p-3 border border-gray-100">
        <p>
          <strong>ℹ️ About Mock Trading:</strong> Entry and exit prices are fetched live from
          Kite LTP. No real orders are ever placed. Max {MAX_SLOTS} concurrent positions.
          P&L = (LTP − Entry) × Quantity. Each lot = 1 unit here — multiply by lot size for real value.
        </p>
      </div>
    </div>
  );
}
