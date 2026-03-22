import React, { useState, useEffect } from 'react';
import { tradingAPI, analysisAPI } from '../services/api';
import { useAuth } from '../services/AuthContext';

// ─── Helpers ────────────────────────────────────────────────────────────────

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

const PREDICTION_CFG = {
  STRONG_BUY:        { cls: 'bg-green-100 text-green-800',  label: '🚀 Strong Buy' },
  BUY:               { cls: 'bg-blue-100  text-blue-800',   label: '📈 Buy' },
  HOLD:              { cls: 'bg-gray-100  text-gray-700',   label: '⏸ Hold' },
  MIXED:             { cls: 'bg-yellow-100 text-yellow-800',label: '⚠ Mixed' },
  INSUFFICIENT_DATA: { cls: 'bg-gray-50   text-gray-400',   label: '— No history' },
};

const RESET = { instrument: '', quantity: '', price: '', order_type: 'BUY', stop_loss_percentage: 3, lot_size: 1 };

const CriterionRow = ({ text }) => {
  const met = /oversold|recovering|crossover|momentum increasing|uptrend|slightly up|agrees/i.test(text);
  return (
    <div className="flex items-start gap-1.5 py-0.5">
      <span className={`mt-0.5 text-xs font-bold ${met ? 'text-green-500' : 'text-red-400'}`}>{met ? '✓' : '✗'}</span>
      <span className={`text-xs leading-snug ${met ? 'text-green-700' : 'text-gray-500'}`}>{text}</span>
    </div>
  );
};

// ─── Component ───────────────────────────────────────────────────────────────

const Orders = () => {
  const { user } = useAuth();
  const [orders, setOrders]               = useState([]);
  const [loading, setLoading]             = useState(true);
  const [showModal, setShowModal]         = useState(false);
  const [instruments, setInstruments]     = useState([]);
  const [spotPrice, setSpotPrice]         = useState(0);
  const [availableCash, setAvailableCash] = useState(null);
  const [marginsLoading, setMarginsLoading] = useState(false);
  const [analysis, setAnalysis]           = useState(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [form, setForm]                   = useState(RESET);
  const [pageMargins, setPageMargins]     = useState(null);  // margin strip on the page (outside modal)

  useEffect(() => { fetchData(); }, []);

  const fetchData = async () => {
    try {
      const [ordersRes, instRes, marginRes] = await Promise.all([
        tradingAPI.getOrders(),
        tradingAPI.getInstruments(),
        analysisAPI.getMargins().catch(() => null),
      ]);
      setOrders(ordersRes.data || []);
      setInstruments(instRes.data.instruments || []);
      setSpotPrice(instRes.data.spot_price || 0);
      if (marginRes?.data) {
        setPageMargins(marginRes.data);
        setAvailableCash(marginRes.data.available_cash);
      }
    } catch (e) {
      console.error('fetchData error', e);
    } finally {
      setLoading(false);
    }
  };

  const openModal = async () => {
    setShowModal(true);
    // Use already-fetched value; refresh in background
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
    const lotSize  = selected?.lot_size || 1;
    const maxQty   = price && availableCash
      ? Math.floor(Math.min(availableCash, 10000) / price)
      : '';
    setForm(f => ({ ...f, instrument: symbol, price: price ? price.toFixed(2) : '', quantity: maxQty, lot_size: lotSize }));
    setAnalysis(null);
    if (!symbol || !selected?.instrument_token) return;
    // Skip analysis for zero-price (deep OTM / illiquid) instruments — Kite has no data for them
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
      price:                parseFloat(form.price),
      order_type:           form.order_type,
      stop_loss_percentage: form.stop_loss_percentage,
    };
    const val = orderData.quantity * orderData.price;
    if (val > 10000) {
      alert(`Order value ₹${val.toFixed(2)} exceeds the ₹10,000 per-trade cap.`);
      return;
    }
    if (availableCash !== null && val > availableCash) {
      alert(`Insufficient balance. Order value ₹${val.toFixed(2)} exceeds available funds ₹${availableCash.toFixed(2)}.`);
      return;
    }
    try {
      await tradingAPI.placeOrder(orderData);
      closeModal();
      fetchData();
      alert('Order placed successfully!');
    } catch (err) {
      alert('Failed: ' + (err.response?.data?.detail || err.message));
    }
  };

  // Derived
  const orderValue   = parseFloat(form.quantity || 0) * parseFloat(form.price || 0);
  // Cap is the stricter of ₹10,000 or available balance
  const effectiveCap = availableCash !== null ? Math.min(10000, availableCash) : 10000;
  const maxQty       = form.price > 0
    ? Math.floor(effectiveCap / parseFloat(form.price))
    : null;
  const exceedsBalance = availableCash !== null && orderValue > availableCash;
  const exceedsCap     = orderValue > 10000;
  const orderBlocked   = exceedsBalance || exceedsCap;
  const slTrigger  = form.price
    ? (parseFloat(form.price) * (1 - form.stop_loss_percentage / 100)).toFixed(2)
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
          <p className="mt-1 text-gray-500">Manage and place your options trades</p>
        </div>
        <button
          onClick={openModal}
          className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2.5 rounded-lg text-sm font-semibold shadow-sm"
        >
          + Place New Order
        </button>
      </div>

      {/* ─── Margin / capital strip (always visible) ─────────────── */}
      <div className="mb-6 grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          {
            label : 'Available Cash',
            value : availableCash !== null
              ? `₹${availableCash.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
              : '—',
            color : availableCash > 5000 ? 'border-green-400 text-green-700 bg-green-50'
                   : availableCash > 1000 ? 'border-yellow-400 text-yellow-700 bg-yellow-50'
                   : 'border-red-300 text-red-700 bg-red-50',
          },
          {
            label : 'Max trade cap',
            value : '₹10,000',
            color : 'border-gray-200 text-gray-600 bg-gray-50',
          },
          {
            label : 'Orders you can afford',
            value : availableCash !== null
              ? `~${Math.floor(Math.min(availableCash, 10000) / Math.max(1, instruments.reduce((s,i)=>s+(i.last_price||0),0)/Math.max(1,instruments.length)))} orders`
              : '—',
            color : 'border-gray-200 text-gray-600 bg-gray-50',
          },
          {
            label : 'Open positions',
            value : orders.filter(o => o.order_type==='BUY' && o.status==='EXECUTED' && !o.exit_price).length,
            color : 'border-gray-200 text-gray-600 bg-gray-50',
          },
        ].map(s => (
          <div key={s.label} className={`rounded-lg border px-4 py-3 ${s.color}`}>
            <p className="text-xs font-medium opacity-70">{s.label}</p>
            <p className="text-lg font-bold">{s.value}</p>
          </div>
        ))}
      </div>

      {/* ═══════════════════════════════════════════════════════════════
          SMART ORDER MODAL
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
                    💰 ₹{availableCash.toLocaleString('en-IN', { maximumFractionDigits: 0 })} available
                  </span>
                ) : (
                  <span className="text-xs text-gray-400">Balance unavailable</span>
                )}
                <button onClick={closeModal} className="text-gray-400 hover:text-gray-600 text-lg font-bold">✕</button>
              </div>
            </div>

            <form onSubmit={handlePlaceOrder}>
              <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6">

                {/* ── LEFT: Order form ────────────────────────────── */}
                <div className="space-y-4">

                  {/* Instrument */}
                  <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                      Instrument
                      {spotPrice > 0 && (
                        <span className="ml-2 normal-case font-normal text-gray-400">
                          Spot ₹{spotPrice.toLocaleString('en-IN')}
                        </span>
                      )}
                    </label>
                    <select
                      value={form.instrument}
                      onChange={e => handleInstrumentChange(e.target.value)}
                      required
                      className="block w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">Select instrument…</option>
                      {(() => {
                        const { ITM, ATM, OTM } = groupByMoneyness(instruments, spotPrice);
                        const opt = (inst, i) => (
                          <option key={i} value={inst.tradingsymbol}>
                            {inst.tradingsymbol} — ₹{inst.last_price ? inst.last_price.toFixed(2) : '0.00'} | Strike ₹{inst.strike?.toLocaleString('en-IN')}
                          </option>
                        );
                        return [
                          ATM.length > 0 && <optgroup key="atm" label="⬛ ATM — At the Money">{ATM.map(opt)}</optgroup>,
                          ITM.length > 0 && <optgroup key="itm" label="🟢 ITM — In the Money (strike < spot)">{ITM.map(opt)}</optgroup>,
                          OTM.length > 0 && <optgroup key="otm" label="🔴 OTM — Out of the Money (strike > spot)">{OTM.map(opt)}</optgroup>,
                        ];
                      })()}
                    </select>
                  </div>

                  {/* BUY / SELL toggle */}
                  <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Order Type</label>
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

                  {/* Price — option premium */}
                  <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                      Option Premium (₹ per unit)
                      <span className="ml-1 normal-case font-normal text-gray-400 text-xs">
                        — price you pay to buy 1 unit of this call option
                      </span>
                    </label>
                    <input type="number" step="0.05" min="0.05" required
                      value={form.price}
                      onChange={e => setForm(f => ({ ...f, price: e.target.value }))}
                      className="block w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    {form.price && form.lot_size > 1 && (
                      <p className="text-xs text-gray-400 mt-0.5">
                        Lot size: {form.lot_size} units →{' '}
                        <span className="font-medium text-gray-600">
                          1 lot costs ₹{(parseFloat(form.price || 0) * form.lot_size).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                        </span>
                      </p>
                    )}
                  </div>

                  {/* Quantity */}
                  <div>
                    <div className="flex justify-between mb-1">
                      <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Quantity</label>
                      {maxQty !== null && (
                        <button type="button"
                          onClick={() => setForm(f => ({ ...f, quantity: maxQty }))}
                          className="text-xs text-blue-600 hover:underline font-medium"
                        >
                          Use max ({maxQty})
                        </button>
                      )}
                    </div>
                    <input type="number" min="1" required
                      value={form.quantity}
                      onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))}
                      className="block w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>

                  {/* Stop Loss */}
                  <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                      Stop Loss % <span className="normal-case font-normal text-gray-400">(triggers at ₹{slTrigger})</span>
                    </label>
                    <input type="number" step="0.5" min="0.5" max="50"
                      value={form.stop_loss_percentage}
                      onChange={e => setForm(f => ({ ...f, stop_loss_percentage: parseFloat(e.target.value) }))}
                      className="block w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>

                  {/* Order value box */}
                  {form.quantity && form.price && (
                    <div className={`rounded-lg p-3 text-sm border ${orderBlocked ? 'bg-red-50 border-red-200' : 'bg-blue-50 border-blue-100'}`}>
                      <div className="flex justify-between font-medium">
                        <span className="text-gray-600">Order value</span>
                        <span className={orderBlocked ? 'text-red-600 font-bold' : 'text-gray-900'}>
                          ₹{orderValue.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </span>
                      </div>
                      {availableCash !== null && (
                        <>
                          <div className="flex justify-between mt-1 text-xs">
                            <span className="text-gray-500">Available cash</span>
                            <span className="font-semibold text-gray-700">
                              ₹{availableCash.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                            </span>
                          </div>
                          <div className="flex justify-between mt-0.5 text-xs">
                            <span className="text-gray-500">Remaining after order</span>
                            <span className={availableCash - orderValue < 0 ? 'text-red-600 font-bold' : 'text-green-600 font-semibold'}>
                              ₹{(availableCash - orderValue).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                            </span>
                          </div>
                        </>
                      )}
                      {exceedsCap && (
                        <div className="mt-2 p-2 bg-red-100 rounded text-xs text-red-700 font-semibold">
                          ⚠ Exceeds ₹10,000 per-trade cap<br/>
                          You need to reduce by ₹{(orderValue - 10000).toLocaleString('en-IN', { maximumFractionDigits: 0 })} (lower qty or price)
                        </div>
                      )}
                      {!exceedsCap && exceedsBalance && availableCash !== null && (
                        <div className="mt-2 p-2 bg-red-100 rounded text-xs text-red-700 font-semibold">
                          ⚠ Insufficient balance<br/>
                          You need ₹{(orderValue - availableCash).toLocaleString('en-IN', { maximumFractionDigits: 0 })} more to place this order
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* ── RIGHT: Analysis panel ────────────────────────── */}
                <div className="min-h-48">
                  {/* Deep OTM / zero-price warning */}
                  {form.instrument && parseFloat(form.price || 0) === 0 && !analysisLoading && (
                    <div className="mb-3 p-3 bg-orange-50 border border-orange-200 rounded-xl text-xs text-orange-700">
                      <p className="font-bold mb-1">⚠ No market activity on this strike</p>
                      <p>The option premium is ₹0.00 — this strike is deep Out-of-The-Money (OTM) with no buyers or sellers.</p>
                      <p className="mt-1 font-medium">Select an ATM or near-ATM strike (closest to spot price) for valid analysis and tradeable prices.</p>
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
                      <p className="text-xs text-red-500 mt-1">Your Kite access token has expired (tokens reset daily).</p>
                      <p className="text-xs text-gray-500 mt-2">Go to <strong>Dashboard → Connect Kite</strong> to re-authenticate, then try again.</p>
                    </div>
                  ) : analysis?.signal?.reasons?.[0]?.includes('No hourly data') || analysis?.signal?.reasons?.[0]?.includes('Insufficient') ? (
                    <div className="border-2 border-dashed border-yellow-200 rounded-xl p-4 text-center">
                      <p className="text-2xl mb-2">📅</p>
                      <p className="text-sm font-bold text-yellow-700">Analysis Unavailable</p>
                      <p className="text-xs text-yellow-600 mt-1">{analysis.signal.reasons[0]}</p>
                      <p className="text-xs text-gray-400 mt-2">You can still place the order manually if you know the price.</p>
                    </div>
                  ) : analysis ? (
                    <div className="space-y-3">

                      {/* Direction badge + confidence */}
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

                      {/* Prediction + success rate */}
                      <div className="flex flex-wrap items-center gap-2">
                        {(() => {
                          const cfg = PREDICTION_CFG[analysis.prediction?.prediction] || PREDICTION_CFG.HOLD;
                          return <span className={`text-xs font-bold px-2 py-0.5 rounded ${cfg.cls}`}>{cfg.label}</span>;
                        })()}
                        {analysis.success_rate !== null ? (
                          <span className="text-xs text-gray-500">
                            📊 <span className="font-semibold text-gray-700">{analysis.success_rate}% success rate</span>
                            <span className="text-gray-400"> / {analysis.signal_count} signals</span>
                          </span>
                        ) : (
                          <span className="text-xs text-gray-400">No history yet — first evaluation</span>
                        )}
                      </div>

                      {/* Queue streak / trend */}
                      {analysis.prediction?.current_streak && (
                        <div className="text-xs text-gray-500 flex gap-3">
                          <span>
                            Streak: <strong className="text-gray-700">
                              {analysis.prediction.current_streak.count}× {analysis.prediction.current_streak.direction}
                            </strong>
                          </span>
                          <span>
                            Trend:{' '}
                            <strong className={
                              analysis.prediction.confidence_trend === 'RISING'  ? 'text-green-600' :
                              analysis.prediction.confidence_trend === 'FALLING' ? 'text-red-500'  : 'text-gray-600'
                            }>
                              {analysis.prediction.confidence_trend}
                            </strong>
                          </span>
                          <span>
                            Buy: <strong className="text-gray-700">
                              {analysis.prediction.buy_signals}/{analysis.prediction.signal_count}
                            </strong>
                          </span>
                        </div>
                      )}

                      {/* Criteria checklist */}
                      <div className="border-t border-gray-100 pt-3">
                        <p className="text-xs font-bold text-gray-400 uppercase tracking-wide mb-1.5">Signal Criteria</p>
                        <div className="space-y-0">
                          {analysis.signal.reasons?.map((r, i) => <CriterionRow key={i} text={r} />)}
                        </div>
                      </div>

                      {/* Indicators row */}
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

                      {/* Suggested SL */}
                      {analysis.signal.suggested_sl && (
                        <p className="text-xs text-gray-400 border-t border-gray-100 pt-2">
                          Engine suggested SL: <span className="font-semibold text-red-500">₹{analysis.signal.suggested_sl}</span>
                        </p>
                      )}
                    </div>
                  ) : (
                    <div className="h-full flex items-center justify-center border-2 border-dashed border-gray-200 rounded-xl p-6 text-center text-gray-400">
                      <p className="text-sm">Analysis unavailable.<br/>You can still place the order manually.</p>
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
                  Place {form.order_type} Order
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
                  {['ID', 'Instrument', 'Type', 'Qty', 'Price', 'Status', 'P&L', 'Created'].map(h => (
                    <th key={h} className="px-5 py-3 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-50">
                {orders.map(order => (
                  <tr key={order.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3 font-medium text-gray-700">#{order.id}</td>
                    <td className="px-5 py-3 font-mono text-xs text-gray-800">{order.instrument}</td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${
                        order.order_type === 'BUY' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                      }`}>{order.order_type}</span>
                    </td>
                    <td className="px-5 py-3 text-gray-600">{order.quantity}</td>
                    <td className="px-5 py-3 text-gray-600">₹{order.price}</td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                        order.status === 'EXECUTED' ? 'bg-green-100 text-green-700' :
                        order.status === 'PENDING'  ? 'bg-yellow-100 text-yellow-700' :
                        'bg-red-100 text-red-700'
                      }`}>{order.status}</span>
                    </td>
                    <td className={`px-5 py-3 font-semibold ${(order.profit_loss || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      ₹{(order.profit_loss || 0).toFixed(2)}
                    </td>
                    <td className="px-5 py-3 text-gray-400 text-xs">{formatDate(order.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default Orders;
