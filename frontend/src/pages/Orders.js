import React, { useState, useEffect, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { tradingAPI, analysisAPI } from '../services/api';
import { useAuth } from '../services/AuthContext';

// ─── Constants ────────────────────────────────────────────────────────────────

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'NIFTYNXT50', 'MIDCPNIFTY'];

const PREDICTION_CFG = {
  STRONG_BUY:        { cls: 'bg-green-100 text-green-800',  label: '🚀 Strong Buy' },
  BUY:               { cls: 'bg-blue-100  text-blue-800',   label: '📈 Buy' },
  HOLD:              { cls: 'bg-gray-100  text-gray-700',   label: '⏸ Hold' },
  MIXED:             { cls: 'bg-yellow-100 text-yellow-800',label: '⚠ Mixed' },
  INSUFFICIENT_DATA: { cls: 'bg-gray-50   text-gray-400',   label: '— No history' },
};

const RESET = {
  instrument: '',
  quantity: 1,
  price: '',
  lot_size: 1,
  order_type: 'BUY',
  stop_loss_percentage: 3,
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

const groupByMoneyness = (instruments, spotPrice) => {
  if (instruments.length === 0) return { ITM: [], ATM: [], OTM: [] };
  const sorted = [...instruments].sort((a, b) => a.strike - b.strike);
  const reference = spotPrice > 0
    ? spotPrice
    : sorted[Math.floor(sorted.length / 2)].strike;
  let atmIdx = 0, minDiff = Infinity;
  sorted.forEach((inst, i) => {
    const d = Math.abs(inst.strike - reference);
    if (d < minDiff) { minDiff = d; atmIdx = i; }
  });
  const ATM_BAND = 2;
  const lo = Math.max(0, atmIdx - ATM_BAND);
  const hi = Math.min(sorted.length - 1, atmIdx + ATM_BAND);
  return { ITM: sorted.slice(0, lo), ATM: sorted.slice(lo, hi + 1), OTM: sorted.slice(hi + 1) };
};

const fmt = (n) => n != null ? `₹${Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';

const CriterionRow = ({ text }) => {
  const met = /oversold|recovering|crossover|momentum increasing|uptrend|slightly up|agrees/i.test(text);
  return (
    <div className="flex items-start gap-1.5 py-0.5">
      <span className={`mt-0.5 text-xs font-bold ${met ? 'text-green-500' : 'text-red-400'}`}>{met ? '✓' : '✗'}</span>
      <span className={`text-xs leading-snug ${met ? 'text-green-700' : 'text-gray-500'}`}>{text}</span>
    </div>
  );
};

// ─── Component ────────────────────────────────────────────────────────────────

const Orders = () => {
  const { user } = useAuth();
  const location = useLocation();

  const [orders, setOrders]                   = useState([]);
  const [loading, setLoading]                 = useState(true);
  const [showModal, setShowModal]             = useState(false);
  const [instruments, setInstruments]         = useState([]);
  const [spotPrice, setSpotPrice]             = useState(0);
  const [availableCash, setAvailableCash]     = useState(null);
  const [marginsLoading, setMarginsLoading]   = useState(false);
  const [analysis, setAnalysis]               = useState(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [form, setForm]                       = useState(RESET);
  const [pageMargins, setPageMargins]         = useState(null);
  const [underlying, setUnderlying]           = useState('NIFTY');
  const [loadingInstruments, setLoadingInstruments] = useState(false);

  // Pending deep-link instrument
  const [pendingInstrument, setPendingInstrument] = useState(null);

  // Action states for table rows
  const [syncingId, setSyncingId]   = useState(null);
  const [exitingId, setExitingId]   = useState(null);
  const [actionMsg, setActionMsg]   = useState('');
  const [actionErr, setActionErr]   = useState('');

  useEffect(() => { fetchOrders(); fetchMargins(); }, []);

  // Deep-link: ?instrument=NIFTY25JUN24500CE
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const sym = params.get('instrument');
    if (sym) setPendingInstrument(sym.toUpperCase());
  }, [location.search]);

  // Once instruments have loaded and there's a pending deep-link, open modal
  useEffect(() => {
    if (!pendingInstrument || instruments.length === 0 || loading) return;
    setShowModal(true);
    handleInstrumentChange(pendingInstrument);
    setPendingInstrument(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingInstrument, instruments, loading]);

  // Re-load instruments when underlying changes
  useEffect(() => {
    if (showModal || pendingInstrument) loadInstrumentsForUnderlying(underlying);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlying]);

  const fetchOrders = async () => {
    try {
      const res = await tradingAPI.getOrders();
      setOrders(res.data || []);
    } catch (e) {
      console.error('fetchOrders error', e);
    } finally {
      setLoading(false);
    }
  };

  const fetchMargins = async () => {
    try {
      const res = await analysisAPI.getMargins();
      if (res?.data) {
        setPageMargins(res.data);
        setAvailableCash(res.data.available_cash);
      }
    } catch (_) {}
  };

  const loadInstrumentsForUnderlying = async (under) => {
    setLoadingInstruments(true);
    try {
      const instRes = await tradingAPI.getInstruments(under);
      setInstruments(instRes.data.instruments || []);
      setSpotPrice(instRes.data.spot_price || 0);
    } catch (e) {
      console.error('loadInstruments error', e);
    } finally {
      setLoadingInstruments(false);
    }
  };

  const openModal = async () => {
    setShowModal(true);
    setForm(RESET);
    setAnalysis(null);
    // Load instruments for current underlying
    if (instruments.length === 0) {
      loadInstrumentsForUnderlying(underlying);
    }
    // Refresh margins
    if (availableCash === null) {
      setMarginsLoading(true);
      try {
        const res = await analysisAPI.getMargins();
        setAvailableCash(res.data.available_cash);
        setPageMargins(res.data);
      } catch { setAvailableCash(null); }
      finally { setMarginsLoading(false); }
    }
  };

  const closeModal = () => { setShowModal(false); setForm(RESET); setAnalysis(null); };

  const handleInstrumentChange = async (symbol) => {
    const selected = instruments.find(i => i.tradingsymbol === symbol);
    const price    = selected?.last_price || '';
    const lotSize  = selected?.lot_size   || 1;
    // Start with 1 lot; auto-set to max affordable given available balance
    const maxLots  = price && availableCash
      ? Math.max(1, Math.floor(availableCash / (price * lotSize)))
      : 1;
    setForm(f => ({
      ...f,
      instrument: symbol,
      price:      price ? price.toFixed(2) : '',
      lot_size:   lotSize,
      quantity:   maxLots,
    }));
    setAnalysis(null);
    if (!symbol || !selected?.instrument_token) return;
    if (!selected.last_price || selected.last_price === 0) return;
    setAnalysisLoading(true);
    try {
      const res = await analysisAPI.analyzeInstrument(symbol, selected.instrument_token);
      setAnalysis(res.data);
    } catch (e) { console.error('analysis error', e); }
    finally { setAnalysisLoading(false); }
  };

  const handlePlaceOrder = async (e) => {
    e.preventDefault();
    const orderData = {
      instrument:           form.instrument,
      quantity:             parseInt(form.quantity),
      lot_size:             parseInt(form.lot_size) || 1,
      price:                parseFloat(form.price),
      order_type:           form.order_type,
      stop_loss_percentage: parseFloat(form.stop_loss_percentage),
    };
    const kiteQty  = orderData.quantity * orderData.lot_size;
    const orderVal = orderData.quantity * orderData.lot_size * orderData.price;
    if (availableCash !== null && orderVal > availableCash) {
      alert(`Insufficient balance. Order value ₹${orderVal.toFixed(2)} > available ₹${availableCash.toFixed(2)}.`);
      return;
    }
    try {
      const res = await tradingAPI.placeOrder(orderData);
      const d = res.data;
      closeModal();
      fetchOrders();
      // Show SL confirmation
      const slMsg = d.sl_trigger_price
        ? ` SL-M order placed at ₹${d.sl_trigger_price}.`
        : ' (No SL placed.)';
      setActionMsg(
        `✅ ${orderData.order_type} order placed on Kite! ${kiteQty} units of ${orderData.instrument} @ ₹${orderData.price}.${slMsg} Broker ID: ${d.broker_order_id}`
      );
      setTimeout(() => setActionMsg(''), 15000);
    } catch (err) {
      setActionErr('❌ ' + (err.response?.data?.detail || err.message));
      setTimeout(() => setActionErr(''), 8000);
    }
  };

  // Sync a PENDING order's status with Kite
  const handleSyncStatus = async (orderId) => {
    setSyncingId(orderId);
    try {
      const res = await tradingAPI.syncOrderStatus(orderId);
      const d = res.data;
      setActionMsg(`↻ Synced order #${orderId}: ${d.prev_status} → ${d.new_status}`);
      fetchOrders();
    } catch (err) {
      setActionErr('Sync failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSyncingId(null);
      setTimeout(() => { setActionMsg(''); setActionErr(''); }, 6000);
    }
  };

  // Manually exit an open BUY position
  const handleExit = async (orderId, instrument) => {
    if (!window.confirm(`Exit position ${instrument} at current market price?`)) return;
    setExitingId(orderId);
    try {
      const res = await tradingAPI.exitPosition(orderId);
      const d = res.data;
      setActionMsg(
        `✅ Exited ${d.instrument}: entry ₹${d.entry_price?.toFixed(2)} → exit ₹${d.exit_price?.toFixed(2)}, P&L: ${d.profit_loss >= 0 ? '+' : ''}₹${d.profit_loss?.toFixed(2)}`
      );
      fetchOrders();
    } catch (err) {
      setActionErr('Exit failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setExitingId(null);
      setTimeout(() => { setActionMsg(''); setActionErr(''); }, 10000);
    }
  };

  // ── Derived values ──────────────────────────────────────────────────────────
  const lotSize    = parseInt(form.lot_size) || 1;
  const kiteQty    = parseInt(form.quantity || 0) * lotSize;
  const orderValue = kiteQty * parseFloat(form.price || 0);

  const maxLotsForCap  = availableCash !== null && form.price > 0
    ? Math.floor(availableCash / (parseFloat(form.price) * lotSize))
    : null;
  const exceedsBalance = availableCash !== null && orderValue > availableCash;
  const orderBlocked   = exceedsBalance;

  const slTrigger = form.price
    ? (parseFloat(form.price) * (1 - parseFloat(form.stop_loss_percentage) / 100)).toFixed(2)
    : '—';

  const formatDate = d => new Date(d).toLocaleString('en-IN');

  if (loading) return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-blue-500" />
    </div>
  );

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

      {/* Page header */}
      <div className="mb-4 flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Orders</h1>
          <p className="mt-1 text-gray-500">Place real options orders on Zerodha Kite</p>
        </div>
        <button
          onClick={openModal}
          className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2.5 rounded-lg text-sm font-semibold shadow-sm"
        >
          + Place New Order
        </button>
      </div>

      {/* Action feedback banners */}
      {actionMsg && (
        <div className="mb-4 px-4 py-3 rounded-lg bg-green-50 border border-green-200 text-green-800 text-sm font-medium">
          {actionMsg}
        </div>
      )}
      {actionErr && (
        <div className="mb-4 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
          {actionErr}
        </div>
      )}

      {/* ─── Margin strip ───────────────────────────────────────────── */}
      <div className="mb-6 grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          {
            label: 'Available Cash',
            value: availableCash !== null ? fmt(availableCash) : '—',
            color: availableCash > 5000 ? 'border-green-400 text-green-700 bg-green-50'
                  : availableCash > 1000 ? 'border-yellow-400 text-yellow-700 bg-yellow-50'
                  : 'border-red-300 text-red-700 bg-red-50',
          },
          {
            label: 'Used in orders',
            value: fmt(orders.reduce((s, o) => s + (o.price || 0) * (o.quantity || 0), 0)),
            color: 'border-gray-200 text-gray-600 bg-gray-50',
          },
          {
            label: 'Open BUY positions',
            value: orders.filter(o => o.order_type === 'BUY' && o.status === 'EXECUTED' && !o.exit_price).length,
            color: 'border-gray-200 text-gray-600 bg-gray-50',
          },
          {
            label: 'Total orders today',
            value: orders.length,
            color: 'border-gray-200 text-gray-600 bg-gray-50',
          },
        ].map(s => (
          <div key={s.label} className={`rounded-lg border px-4 py-3 ${s.color}`}>
            <p className="text-xs font-medium opacity-70">{s.label}</p>
            <p className="text-lg font-bold">{s.value}</p>
          </div>
        ))}
      </div>

      {/* ═══════════════════════════════════════════════════════════════
          ORDER PLACEMENT MODAL
      ═══════════════════════════════════════════════════════════════ */}
      {showModal && (
        <div className="fixed inset-0 bg-gray-900 bg-opacity-60 z-50 flex items-start justify-center overflow-y-auto py-10 px-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl">

            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
              <h2 className="text-lg font-bold text-gray-900">Place New Order</h2>
              <div className="flex items-center gap-3">
                {marginsLoading ? (
                  <div className="h-6 w-36 animate-pulse bg-gray-100 rounded-full" />
                ) : availableCash !== null ? (
                  <span className="text-sm font-semibold bg-green-50 border border-green-200 text-green-700 px-3 py-1 rounded-full">
                    💰 {fmt(availableCash)} available
                  </span>
                ) : (
                  <span className="text-xs text-gray-400">Balance unavailable</span>
                )}
                <button onClick={closeModal} className="text-gray-400 hover:text-gray-600 text-lg font-bold">✕</button>
              </div>
            </div>

            <form onSubmit={handlePlaceOrder}>
              <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6">

                {/* ── LEFT: Order form ──────────────────────────────────── */}
                <div className="space-y-4">

                  {/* Underlying selector */}
                  <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                      Index (Underlying)
                    </label>
                    <div className="flex gap-1.5 flex-wrap">
                      {UNDERLYINGS.map(u => (
                        <button
                          key={u}
                          type="button"
                          onClick={() => {
                            setUnderlying(u);
                            setForm(f => ({ ...f, instrument: '', price: '', lot_size: 1, quantity: 1 }));
                            setAnalysis(null);
                            loadInstrumentsForUnderlying(u);
                          }}
                          className={`px-3 py-1 rounded-md text-xs font-semibold border transition-colors ${
                            underlying === u
                              ? 'bg-blue-600 text-white border-blue-600'
                              : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
                          }`}
                        >
                          {u}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Instrument dropdown */}
                  <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                      Instrument (CE Option)
                      {spotPrice > 0 && (
                        <span className="ml-2 normal-case font-normal text-gray-400">
                          Spot ₹{spotPrice.toLocaleString('en-IN')}
                        </span>
                      )}
                    </label>
                    {loadingInstruments ? (
                      <div className="h-10 animate-pulse bg-gray-100 rounded-lg" />
                    ) : (
                      <select
                        value={form.instrument}
                        onChange={e => handleInstrumentChange(e.target.value)}
                        required
                        className="block w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                      >
                        <option value="">Select {underlying} CE instrument…</option>
                        {(() => {
                          const { ITM, ATM, OTM } = groupByMoneyness(instruments, spotPrice);
                          const opt = (inst, i) => (
                            <option key={i} value={inst.tradingsymbol}>
                              {inst.tradingsymbol} — ₹{inst.last_price ? inst.last_price.toFixed(2) : '0.00'} | Strike ₹{inst.strike?.toLocaleString('en-IN')} | Lot {inst.lot_size}
                            </option>
                          );
                          return [
                            ATM.length > 0 && <optgroup key="atm" label="⬛ ATM — At the Money">{ATM.map(opt)}</optgroup>,
                            ITM.length > 0 && <optgroup key="itm" label="🟢 ITM — In the Money">{ITM.map(opt)}</optgroup>,
                            OTM.length > 0 && <optgroup key="otm" label="🔴 OTM — Out of the Money">{OTM.map(opt)}</optgroup>,
                          ];
                        })()}
                      </select>
                    )}
                    {form.lot_size > 1 && (
                      <p className="text-xs text-gray-400 mt-0.5">
                        Lot size: <strong>{form.lot_size} units per lot</strong>
                      </p>
                    )}
                  </div>

                  {/* BUY / SELL toggle */}
                  <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Order Direction</label>
                    <div className="flex rounded-lg border border-gray-200 overflow-hidden">
                      {['BUY', 'SELL'].map(t => (
                        <button key={t} type="button"
                          onClick={() => setForm(f => ({ ...f, order_type: t }))}
                          className={`flex-1 py-2 text-sm font-semibold transition-colors ${
                            form.order_type === t
                              ? t === 'BUY' ? 'bg-green-600 text-white' : 'bg-red-600 text-white'
                              : 'bg-white text-gray-500 hover:bg-gray-50'
                          }`}
                        >{t}</button>
                      ))}
                    </div>
                  </div>

                  {/* Option premium (limit price) */}
                  <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                      Option Premium — Limit Price (₹ per unit)
                    </label>
                    <input type="number" step="0.05" min="0.05" required
                      value={form.price}
                      onChange={e => setForm(f => ({ ...f, price: e.target.value }))}
                      className="block w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <p className="text-xs text-gray-400 mt-0.5">
                      This is the maximum premium you're willing to pay per unit of the option.
                    </p>
                  </div>

                  {/* Quantity in LOTS */}
                  <div>
                    <div className="flex justify-between mb-1">
                      <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
                        Quantity (lots)
                        {kiteQty > 0 && (
                          <span className="ml-2 normal-case font-normal text-blue-600">
                            = {kiteQty} units on Kite
                          </span>
                        )}
                      </label>
                      {maxLotsForCap !== null && (
                        <button type="button"
                          onClick={() => setForm(f => ({ ...f, quantity: maxLotsForCap }))}
                          className="text-xs text-blue-600 hover:underline font-medium"
                        >
                          Max ({maxLotsForCap} lots)
                        </button>
                      )}
                    </div>
                    <input type="number" min="1" max="50" required
                      value={form.quantity}
                      onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))}
                      className="block w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>

                  {/* Stop Loss % */}
                  {form.order_type === 'BUY' && (
                    <div>
                      <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Stop Loss %
                        <span className="normal-case font-normal text-gray-400 ml-1">
                          (SL-M trigger at ₹{slTrigger})
                        </span>
                      </label>
                      <input type="number" step="0.5" min="0.5" max="50"
                        value={form.stop_loss_percentage}
                        onChange={e => setForm(f => ({ ...f, stop_loss_percentage: parseFloat(e.target.value) }))}
                        className="block w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                      <p className="text-xs text-gray-400 mt-0.5">
                        A SL-M (Stop-Loss Market) SELL order will be placed automatically on Kite.
                      </p>
                    </div>
                  )}

                  {/* Order value summary */}
                  {form.quantity && form.price && (
                    <div className={`rounded-lg p-3 text-sm border ${orderBlocked ? 'bg-red-50 border-red-200' : 'bg-blue-50 border-blue-100'}`}>
                      <div className="flex justify-between font-medium">
                        <span className="text-gray-600">Total order value</span>
                        <span className={orderBlocked ? 'text-red-600 font-bold' : 'text-gray-900'}>
                          {fmt(orderValue)}
                        </span>
                      </div>
                      <div className="flex justify-between mt-1 text-xs text-gray-500">
                        <span>Calculation</span>
                        <span className="font-mono">
                          {form.quantity} lots × {lotSize} units × ₹{parseFloat(form.price || 0).toFixed(2)}
                        </span>
                      </div>
                      {availableCash !== null && (
                        <div className="flex justify-between mt-0.5 text-xs">
                          <span className="text-gray-500">Remaining after order</span>
                          <span className={availableCash - orderValue < 0 ? 'text-red-600 font-bold' : 'text-green-600 font-semibold'}>
                            {fmt(availableCash - orderValue)}
                          </span>
                        </div>
                      )}
                      {exceedsBalance && (
                        <div className="mt-2 p-2 bg-red-100 rounded text-xs text-red-700 font-semibold">
                          ⚠ Insufficient balance — need {fmt(orderValue - availableCash)} more.
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* ── RIGHT: Analysis panel ─────────────────────────────── */}
                <div className="min-h-48">
                  {form.instrument && parseFloat(form.price || 0) === 0 && !analysisLoading && (
                    <div className="mb-3 p-3 bg-orange-50 border border-orange-200 rounded-xl text-xs text-orange-700">
                      <p className="font-bold mb-1">⚠ No market activity on this strike</p>
                      <p>The option premium is ₹0.00 — this strike is deep OTM with no buyers or sellers. Select an ATM strike for analysis and a tradeable price.</p>
                    </div>
                  )}
                  {!form.instrument ? (
                    <div className="h-full flex flex-col items-center justify-center text-center border-2 border-dashed border-gray-200 rounded-xl p-6 text-gray-400">
                      <p className="text-3xl mb-2">📊</p>
                      <p className="text-sm">Select an instrument to see<br/>live signal analysis &amp; success rate</p>
                    </div>
                  ) : analysisLoading ? (
                    <div className="h-full flex flex-col items-center justify-center gap-3">
                      <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500" />
                      <p className="text-sm text-gray-500">Analysing {form.instrument}…</p>
                    </div>
                  ) : analysis?.signal?.session_expired ? (
                    <div className="border-2 border-dashed border-red-200 rounded-xl p-4 text-center">
                      <p className="text-2xl mb-2">🔑</p>
                      <p className="text-sm font-bold text-red-700">Kite Session Expired</p>
                      <p className="text-xs text-gray-500 mt-2">Go to <strong>Dashboard → Connect Kite</strong> to re-authenticate.</p>
                    </div>
                  ) : analysis ? (
                    <div className="space-y-3">
                      {/* Direction + confidence */}
                      <div className="flex items-center justify-between">
                        <span className={`px-3 py-1 rounded-full text-sm font-bold ${
                          analysis.signal.direction === 'BUY'  ? 'bg-green-100 text-green-800' :
                          analysis.signal.direction === 'SELL' ? 'bg-red-100 text-red-800' :
                          'bg-gray-100 text-gray-600'
                        }`}>
                          {analysis.signal.direction === 'BUY' ? '📈' : analysis.signal.direction === 'SELL' ? '📉' : '⏸'} {analysis.signal.direction}
                        </span>
                        <span className="text-sm text-gray-500">
                          Confidence: <strong className="text-gray-800">{analysis.signal.confidence?.toFixed(1)}%</strong>
                        </span>
                      </div>
                      {/* Confidence bar */}
                      <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                        <div className={`h-full rounded-full transition-all duration-500 ${
                          analysis.signal.confidence >= 70 ? 'bg-green-500' :
                          analysis.signal.confidence >= 55 ? 'bg-yellow-400' : 'bg-red-400'
                        }`} style={{ width: `${Math.min(analysis.signal.confidence, 100)}%` }} />
                      </div>
                      {/* Prediction badge */}
                      <div className="flex flex-wrap gap-2">
                        {(() => {
                          const cfg = PREDICTION_CFG[analysis.prediction?.prediction] || PREDICTION_CFG.HOLD;
                          return <span className={`text-xs font-bold px-2 py-0.5 rounded ${cfg.cls}`}>{cfg.label}</span>;
                        })()}
                        {analysis.success_rate !== null ? (
                          <span className="text-xs text-gray-500">
                            📊 <strong>{analysis.success_rate}% success</strong>
                            <span className="text-gray-400"> / {analysis.signal_count} signals</span>
                          </span>
                        ) : (
                          <span className="text-xs text-gray-400">No history yet</span>
                        )}
                      </div>
                      {/* Criteria */}
                      <div className="border-t border-gray-100 pt-3">
                        <p className="text-xs font-bold text-gray-400 uppercase tracking-wide mb-1.5">Signal Criteria</p>
                        <div className="space-y-0">
                          {analysis.signal.reasons?.map((r, i) => <CriterionRow key={i} text={r} />)}
                        </div>
                      </div>
                      {/* Indicators */}
                      {analysis.signal.indicators && (
                        <div className="grid grid-cols-3 gap-2 border-t border-gray-100 pt-3">
                          {[
                            { label: 'RSI', value: analysis.signal.indicators.rsi?.toFixed(1) },
                            { label: 'MACD Hist', value: analysis.signal.indicators.macd_histogram?.toFixed(4) },
                            { label: 'Trend', value: `${analysis.signal.indicators.price_trend_pct?.toFixed(2)}%` },
                          ].map(({ label, value }) => (
                            <div key={label} className="bg-gray-50 rounded-lg p-2 text-center">
                              <p className="text-xs text-gray-400">{label}</p>
                              <p className="text-sm font-bold text-gray-800">{value ?? '—'}</p>
                            </div>
                          ))}
                        </div>
                      )}
                      {analysis.signal.suggested_sl && (
                        <p className="text-xs text-gray-400 border-t pt-2">
                          Engine SL suggestion: <span className="font-semibold text-red-500">₹{analysis.signal.suggested_sl}</span>
                        </p>
                      )}
                    </div>
                  ) : (
                    <div className="h-full flex items-center justify-center border-2 border-dashed border-gray-200 rounded-xl p-6 text-center text-gray-400">
                      <p className="text-sm">Analysis unavailable — you can still place the order manually.</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Footer */}
              <div className="flex gap-3 px-6 pb-6">
                <button
                  type="submit"
                  disabled={orderBlocked || !form.instrument || !form.quantity || !form.price}
                  className="flex-1 py-2.5 rounded-lg text-sm font-bold transition-colors disabled:bg-gray-200 disabled:text-gray-400 disabled:cursor-not-allowed bg-blue-600 hover:bg-blue-700 text-white"
                >
                  {orderBlocked ? '⚠ Cannot Place Order' : `Place ${form.order_type} Order on Kite`}
                </button>
                <button type="button" onClick={closeModal}
                  className="flex-1 py-2.5 rounded-lg text-sm font-semibold bg-gray-100 hover:bg-gray-200 text-gray-700"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ─── Orders table ─────────────────────────────────────────── */}
      <div className="bg-white shadow rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
          <h2 className="text-lg font-semibold text-gray-900">All Orders</h2>
          <span className="text-sm text-gray-400">{orders.length} total</span>
        </div>

        {orders.length === 0 ? (
          <div className="p-10 text-center text-gray-400">
            <p className="text-4xl mb-3">📋</p>
            <p className="font-medium">No orders yet.</p>
            <p className="text-sm mt-1">Place your first order to get started.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-100 text-sm">
              <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wider">
                <tr>
                  <th className="px-4 py-3 text-left font-medium">Instrument</th>
                  <th className="px-4 py-3 text-left font-medium">Type</th>
                  <th className="px-4 py-3 text-left font-medium">Qty (lots)</th>
                  <th className="px-4 py-3 text-left font-medium">Premium</th>
                  <th className="px-4 py-3 text-left font-medium">SL Trigger</th>
                  <th className="px-4 py-3 text-left font-medium">Status</th>
                  <th className="px-4 py-3 text-left font-medium">P&L</th>
                  <th className="px-4 py-3 text-left font-medium">Broker ID</th>
                  <th className="px-4 py-3 text-left font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-50">
                {orders.map(order => {
                  const isOpenBuy = order.order_type === 'BUY' && order.status === 'EXECUTED' && !order.exit_price;
                  const isPending = order.status === 'PENDING';
                  return (
                    <tr key={order.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-mono text-xs text-gray-800 font-semibold">{order.instrument}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${
                          order.order_type === 'BUY' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                        }`}>{order.order_type}</span>
                      </td>
                      <td className="px-4 py-3 text-gray-600">{order.quantity}</td>
                      <td className="px-4 py-3 text-gray-600">{fmt(order.price)}</td>
                      <td className="px-4 py-3 text-xs">
                        {order.sl_trigger_price
                          ? <span className="text-red-500 font-semibold">{fmt(order.sl_trigger_price)}</span>
                          : <span className="text-gray-300">—</span>
                        }
                      </td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                          order.status === 'EXECUTED'  ? 'bg-green-100 text-green-700' :
                          order.status === 'PENDING'   ? 'bg-yellow-100 text-yellow-700' :
                          order.status === 'REJECTED'  ? 'bg-red-100 text-red-600' :
                          'bg-gray-100 text-gray-600'
                        }`}>{order.status}</span>
                      </td>
                      <td className={`px-4 py-3 font-semibold text-xs ${(order.profit_loss || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {(order.profit_loss || 0) !== 0 ? fmt(order.profit_loss) : '—'}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-400 font-mono">
                        {order.broker_order_id
                          ? <span title={order.broker_order_id}>{order.broker_order_id.slice(-8)}</span>
                          : '—'
                        }
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          {/* Sync status — for PENDING orders */}
                          {isPending && (
                            <button
                              onClick={() => handleSyncStatus(order.id)}
                              disabled={syncingId === order.id}
                              className="px-2 py-1 text-xs rounded bg-indigo-50 text-indigo-600 hover:bg-indigo-100 border border-indigo-200 disabled:opacity-50"
                              title="Check if this order has been filled on Kite"
                            >
                              {syncingId === order.id ? '…' : '↻ Sync'}
                            </button>
                          )}
                          {/* Exit position — for open EXECUTED BUY orders */}
                          {isOpenBuy && (
                            <button
                              onClick={() => handleExit(order.id, order.instrument)}
                              disabled={exitingId === order.id}
                              className="px-2 py-1 text-xs rounded bg-red-50 text-red-600 hover:bg-red-100 border border-red-200 disabled:opacity-50 font-semibold"
                              title="Exit this position at current market price"
                            >
                              {exitingId === order.id ? 'Exiting…' : '🚪 Exit'}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Explanatory footer */}
        <div className="px-6 py-3 bg-gray-50 border-t border-gray-100 text-xs text-gray-400">
          <strong>↻ Sync</strong> — polls Kite for execution status (PENDING → EXECUTED).
          <strong className="ml-3">🚪 Exit</strong> — places a MARKET SELL on Kite and records P&amp;L.
          SL Trigger shows the price at which your automatic Stop-Loss Market order fires.
        </div>
      </div>
    </div>
  );
};

export default Orders;
