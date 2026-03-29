import React, { useState, useEffect, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { tradingAPI, kiteAPI, analysisAPI, mockAPI } from '../services/api';
import { useAuth } from '../services/AuthContext';

const Dashboard = () => {
  const { user } = useAuth();
  const location = useLocation();
  const [summary, setSummary]         = useState(null);
  const [orders, setOrders]           = useState([]);
  const [instruments, setInstruments] = useState([]);
  const [loading, setLoading]         = useState(true);
  const [syncing, setSyncing]         = useState(false);
  const [syncMsg, setSyncMsg]         = useState('');
  // Mock trading snapshot for dashboard tile
  const [mockSnapshot, setMockSnapshot] = useState(null);

  // Detect redirect from a Kite session expiry (any page → /dashboard?kite_expired=1)
  const kiteExpiredRedirect = new URLSearchParams(location.search).get('kite_expired') === '1';
  // Detect redirect from a Kite market data permission error (plan/subscription issue)
  const kiteNoMarketData = new URLSearchParams(location.search).get('kite_no_market_data') === '1';

  const fetchAll = useCallback(async () => {
    try {
      const [summaryRes, ordersRes] = await Promise.all([
        analysisAPI.getDashboardSummary().catch(() => ({ data: null })),
        tradingAPI.getOrders().catch(() => ({ data: [] })),
      ]);
      setSummary(summaryRes.data);
      setOrders(ordersRes.data || []);

      // Try instruments only if Kite is linked
      if (summaryRes.data?.kite_linked) {
        try {
          const instRes = await tradingAPI.getInstruments();
          setInstruments(instRes.data.instruments || []);
        } catch (_) {}

        // Fetch mock P&L snapshot (best-effort — don't block dashboard if it fails)
        try {
          const mockRes = await mockAPI.getPnL();
          setMockSnapshot(mockRes.data);
        } catch (_) {}
      }
    } catch (e) {
      console.error('Dashboard fetch error', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleConnectKite = async () => {
    try {
      const res = await kiteAPI.getLoginUrl();
      window.location.href = res.data.login_url;
    } catch (error) {
      alert('Could not get Kite login URL: ' + (error.response?.data?.detail || error.message));
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    setSyncMsg('');
    try {
      const res = await analysisAPI.syncOrdersFromKite();
      const d = res.data;
      setSyncMsg(`✓ Synced ${d.total_kite_orders} Kite orders → ${d.inserted} new, ${d.updated} updated`);
      fetchAll();
    } catch (e) {
      setSyncMsg('✗ Sync failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setSyncing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-blue-500" />
      </div>
    );
  }

  const kiteLinked = summary?.kite_linked ?? false;
  const cash       = summary?.available_cash ?? null;
  const totalPnL   = summary?.total_pnl ?? orders.reduce((s, o) => s + (o.profit_loss || 0), 0);

  // ── Mock snapshot helpers ──────────────────────────────────────
  const mockPositions = mockSnapshot?.positions?.length ?? 0;
  const mockTotalPnl  = mockSnapshot?.total_pnl ?? null;
  const mockUsed      = mockSnapshot?.used_slots ?? 0;

  // ── Stat cards ───────────────────────────────────────────────
  const stats = [
    {
      label   : 'Available Cash',
      value   : cash !== null
        ? `₹${cash.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
        : kiteLinked ? '…' : '—',
      sub     : cash !== null && summary?.total_orders > 0
        ? `~${Math.floor(cash / Math.max(1, orders.reduce((s,o)=>s+(o.price||0),0)/Math.max(1,orders.length)))} more orders`
        : 'Link Kite to see',
      color   : cash > 5000 ? 'bg-green-500' : cash > 1000 ? 'bg-yellow-500' : 'bg-red-500',
      icon    : '💰',
    },
    {
      label   : 'Total Orders',
      value   : summary?.total_orders ?? orders.length,
      sub     : `${summary?.executed_orders ?? 0} executed`,
      color   : 'bg-blue-500',
      icon    : '📋',
    },
    {
      label   : 'Open Positions',
      value   : summary?.open_positions ?? 0,
      sub     : summary?.monitor_running ? '🟢 Monitor ON' : '🔴 Monitor OFF',
      color   : 'bg-purple-500',
      icon    : '📊',
    },
    {
      label   : 'Total P&L',
      value   : `₹${totalPnL.toFixed(2)}`,
      sub     : totalPnL >= 0 ? 'Net profit' : 'Net loss',
      color   : totalPnL >= 0 ? 'bg-green-500' : 'bg-red-500',
      icon    : '₹',
    },
    {
      label   : 'Mock Positions',
      value   : kiteLinked ? `${mockUsed}/5` : '—',
      sub     : mockTotalPnl != null
        ? `P&L: ${mockTotalPnl >= 0 ? '+' : ''}₹${mockTotalPnl.toFixed(2)}`
        : kiteLinked ? 'No open mocks' : 'Link Kite to see',
      color   : mockTotalPnl != null && mockTotalPnl >= 0
        ? 'bg-indigo-500'
        : mockTotalPnl != null
        ? 'bg-orange-500'
        : 'bg-gray-400',
      icon    : '🧪',
      href    : '/mock-trading',
    },
  ];

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

      {/* Header */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Trading Dashboard</h1>
          <p className="mt-1 text-gray-500 text-sm">Monitor your options trading performance</p>
        </div>
        <div className="flex items-center gap-2">
          {kiteLinked && (
            <button
              onClick={handleSync}
              disabled={syncing}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white transition-colors"
            >
              {syncing ? (
                <><span className="animate-spin inline-block">↻</span> Syncing…</>
              ) : (
                <><span>↻</span> Sync from Kite</>
              )}
            </button>
          )}
          <button
            onClick={fetchAll}
            className="px-4 py-2 rounded-lg text-sm font-semibold bg-gray-100 hover:bg-gray-200 text-gray-700"
          >
            ⟳ Refresh
          </button>
        </div>
      </div>

      {/* Sync feedback */}
      {syncMsg && (
        <div className={`mb-4 px-4 py-2 rounded-lg text-sm font-medium border ${
          syncMsg.startsWith('✓') ? 'bg-green-50 text-green-700 border-green-200' : 'bg-red-50 text-red-700 border-red-200'
        }`}>
          {syncMsg}
        </div>
      )}

      {/* Kite session expired banner — shown when redirected from any page after token expiry */}
      {kiteExpiredRedirect && (
        <div className="mb-4 bg-red-50 border-2 border-red-400 rounded-xl p-4 flex items-start gap-4">
          <span className="text-2xl shrink-0">🔑</span>
          <div className="flex-1">
            <p className="font-bold text-red-800 text-sm">Kite Session Expired</p>
            <p className="text-sm text-red-700 mt-0.5">
              Your Kite access token has expired (tokens reset daily). Re-link your account below to restore live data.
            </p>
          </div>
          <button
            onClick={handleConnectKite}
            className="shrink-0 bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg text-sm font-semibold"
          >
            🔗 Re-link Now
          </button>
        </div>
      )}

      {/* Kite market data permission banner — subscription/plan issue, re-linking won't help */}
      {kiteNoMarketData && (
        <div className="mb-4 bg-orange-50 border-2 border-orange-400 rounded-xl p-4 flex items-start gap-4">
          <span className="text-2xl shrink-0">⚠️</span>
          <div className="flex-1">
            <p className="font-bold text-orange-800 text-sm">Kite Connect Personal Plan — No Market Data Access</p>
            <p className="text-sm text-orange-700 mt-1">
              Your API key is on the <strong>free Personal plan</strong> which has no market data access
              (no quotes, no historical candles). Re-linking will <strong>not</strong> fix this.
            </p>
            <p className="text-sm text-orange-700 mt-1">
              Fix: create a <strong>paid Kite Connect app</strong> at{' '}
              <a
                href="https://developers.kite.trade"
                target="_blank"
                rel="noopener noreferrer"
                className="underline font-semibold"
              >
                developers.kite.trade
              </a>
              {' '}(₹500/month). Historical data is <strong>included free</strong> — no extra add-on needed.
              Once you update the API key + secret in your <code className="bg-orange-100 px-1 rounded">.env</code>, everything works automatically.
            </p>
          </div>
        </div>
      )}

      {/* Kite connection banner */}
      {!kiteLinked ? (
        <div className="mb-6 bg-yellow-50 border border-yellow-300 rounded-lg p-4 flex items-center justify-between">
          <div>
            <p className="font-medium text-yellow-800">Zerodha Kite not connected</p>
            <p className="text-sm text-yellow-700 mt-0.5">
              Link your Kite account to view instruments, margin, and place orders.
            </p>
          </div>
          <button
            onClick={handleConnectKite}
            className="ml-4 shrink-0 bg-yellow-500 hover:bg-yellow-600 text-white px-4 py-2 rounded-md text-sm font-medium"
          >
            Connect Kite
          </button>
        </div>
      ) : (
        <div className="mb-6 bg-green-50 border border-green-200 rounded-lg p-3 flex items-center justify-between">
          <p className="text-sm text-green-700 font-medium">✓ Kite account linked</p>
          <button
            onClick={handleConnectKite}
            className="text-xs text-green-600 hover:text-green-800 underline"
          >
            Re-link / Refresh token
          </button>
        </div>
      )}

      {/* Stat Cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 mb-8">
        {stats.map(s => {
          const inner = (
            <>
              <div className="flex items-center gap-3 mb-2">
                <div className={`w-9 h-9 ${s.color} rounded-lg flex items-center justify-center text-white text-sm font-bold shrink-0`}>
                  {s.icon}
                </div>
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{s.label}</p>
              </div>
              <p className="text-2xl font-bold text-gray-900">{s.value}</p>
              <p className="text-xs text-gray-400 mt-0.5">{s.sub}</p>
            </>
          );
          return s.href ? (
            <a
              key={s.label}
              href={s.href}
              className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 hover:border-indigo-300 hover:shadow-md transition-all cursor-pointer no-underline"
            >
              {inner}
            </a>
          ) : (
            <div key={s.label} className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
              {inner}
            </div>
          );
        })}
      </div>

      {/* Quick Actions */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5 mb-6">
        <h2 className="text-base font-semibold text-gray-900 mb-3">Quick Actions</h2>
        <div className="flex flex-wrap gap-3">

          {/* Primary morning action — Win Probability scan */}
          <button
            onClick={() => window.location.href = '/win-probability'}
            className="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded-md text-sm font-semibold flex items-center gap-1.5"
            title="Scan instruments with the 11-factor engine to find the best trade of the day"
          >
            🎯 Scan Win Probability
          </button>

          <button
            onClick={() => window.location.href = '/mock-trading'}
            className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-md text-sm font-medium flex items-center gap-1.5"
          >
            🧪 Mock Trading
          </button>

          <button
            onClick={() => window.location.href = '/orders'}
            className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium"
          >
            + Place Real Order
          </button>

          <button
            onClick={() => window.location.href = '/strategies'}
            className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded-md text-sm font-medium flex items-center gap-1.5"
          >
            📈 Performance
          </button>

          {kiteLinked && (
            <button
              onClick={handleSync}
              disabled={syncing}
              className="border border-indigo-300 text-indigo-600 hover:bg-indigo-50 px-4 py-2 rounded-md text-sm font-medium disabled:opacity-50"
            >
              ↻ Restore Orders from Kite
            </button>
          )}
        </div>
        <p className="text-xs text-gray-400 mt-2">
          Start your day with <strong>Scan Win Probability</strong> → pick Grade A/A+ instruments → open a Mock position to validate → place a real order when confident.
          "Restore Orders from Kite" re-imports today's Kite orders if the database was wiped.
        </p>
      </div>

      {/* Instruments table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5 mb-6">
        <h2 className="text-base font-semibold text-gray-900 mb-3">
          Available Instruments
          <span className="ml-2 text-sm font-normal text-gray-400">({instruments.length} CE options)</span>
        </h2>
        {instruments.length === 0 ? (
          <p className="text-gray-400 text-sm">{kiteLinked ? 'Loading instruments…' : 'Link Kite account to see instruments.'}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm divide-y divide-gray-100">
              <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                <tr>
                  {['Symbol', 'Strike', 'Expiry', 'Lot Size', 'Last Price'].map(h => (
                    <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {instruments.map((inst, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-medium text-gray-900">{inst.tradingsymbol}</td>
                    <td className="px-4 py-2 text-gray-700">₹{inst.strike?.toLocaleString('en-IN')}</td>
                    <td className="px-4 py-2 text-gray-500">
                      {inst.expiry ? new Date(inst.expiry).toLocaleDateString('en-IN', { day:'2-digit', month:'short', year:'numeric' }) : '—'}
                    </td>
                    <td className="px-4 py-2 text-gray-500">{inst.lot_size}</td>
                    <td className="px-4 py-2 font-semibold text-green-600">
                      {inst.last_price != null ? `₹${inst.last_price.toFixed(2)}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent Orders */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
        <h2 className="text-base font-semibold text-gray-900 mb-3">Recent Orders</h2>
        {orders.length === 0 ? (
          <p className="text-gray-400 text-sm">No orders yet. Place your first order to get started.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm divide-y divide-gray-100">
              <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                <tr>
                  {['Instrument', 'Type', 'Qty', 'Price', 'Status', 'P&L'].map(h => (
                    <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {orders.slice(0, 8).map(order => (
                  <tr key={order.id}>
                    <td className="px-4 py-2 font-mono text-xs text-gray-800">{order.instrument}</td>
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${
                        order.order_type === 'BUY' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                      }`}>{order.order_type}</span>
                    </td>
                    <td className="px-4 py-2 text-gray-600">{order.quantity}</td>
                    <td className="px-4 py-2 text-gray-600">₹{order.price}</td>
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                        order.status === 'EXECUTED' ? 'bg-green-100 text-green-700' :
                        order.status === 'PENDING'  ? 'bg-yellow-100 text-yellow-700' :
                        'bg-red-100 text-red-700'
                      }`}>{order.status}</span>
                    </td>
                    <td className={`px-4 py-2 font-semibold ${(order.profit_loss||0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      ₹{(order.profit_loss || 0).toFixed(2)}
                    </td>
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

export default Dashboard;
