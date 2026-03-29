import React, { useState, useEffect, useCallback } from 'react';
import { performanceAPI } from '../services/api';

// ─── Grade meta ───────────────────────────────────────────────────────────────

const GRADE_ORDER = ['A+', 'A', 'B', 'C', 'D', 'Unknown'];

const GRADE_STYLES = {
  'A+':      { bg: 'bg-emerald-100', text: 'text-emerald-800', border: 'border-emerald-300', bar: 'bg-emerald-500' },
  'A':       { bg: 'bg-green-100',   text: 'text-green-800',   border: 'border-green-300',   bar: 'bg-green-500'   },
  'B':       { bg: 'bg-yellow-100',  text: 'text-yellow-800',  border: 'border-yellow-300',  bar: 'bg-yellow-500'  },
  'C':       { bg: 'bg-orange-100',  text: 'text-orange-800',  border: 'border-orange-300',  bar: 'bg-orange-400'  },
  'D':       { bg: 'bg-red-100',     text: 'text-red-800',     border: 'border-red-300',     bar: 'bg-red-400'     },
  'Unknown': { bg: 'bg-gray-100',    text: 'text-gray-600',    border: 'border-gray-300',    bar: 'bg-gray-400'    },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

const fmt = (n, dec = 2) =>
  n == null ? '—' : (n >= 0 ? '+' : '') + n.toFixed(dec);

const pnlColor = (n) =>
  n == null ? 'text-gray-400' : n > 0 ? 'text-emerald-600' : n < 0 ? 'text-red-500' : 'text-gray-500';

// Simple bar — width as % of a given max
const MiniBar = ({ value, max, barClass, height = 'h-2' }) => {
  const pct = max > 0 ? Math.min(100, Math.abs(value / max) * 100) : 0;
  return (
    <div className={`w-full bg-gray-100 rounded-full overflow-hidden ${height}`}>
      <div className={`h-full rounded-full ${barClass}`} style={{ width: `${pct}%` }} />
    </div>
  );
};

// Win-rate donut-style pill
const WinRatePill = ({ winRate }) => {
  if (winRate == null) return <span className="text-gray-400 text-xs">—</span>;
  const cls =
    winRate >= 65 ? 'bg-emerald-100 text-emerald-700 border-emerald-200' :
    winRate >= 50 ? 'bg-yellow-100  text-yellow-700  border-yellow-200'  :
                   'bg-red-100     text-red-700     border-red-200';
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-bold border ${cls}`}>
      {winRate.toFixed(0)}% wins
    </span>
  );
};

// ─── Sub-panels ───────────────────────────────────────────────────────────────

/** Summary KPI strip at the top */
const SummaryStrip = ({ summary }) => {
  const kpis = [
    { label: 'Total trades',  value: summary.total,              sub: null,               color: 'text-gray-800' },
    { label: 'Win rate',      value: summary.win_rate != null ? `${summary.win_rate.toFixed(0)}%` : '—',
                              sub: `${summary.wins}W / ${summary.losses}L`, color: summary.win_rate >= 50 ? 'text-emerald-600' : 'text-red-500' },
    { label: 'Total P&L',    value: `₹${fmt(summary.total_pnl)}`, sub: null,             color: pnlColor(summary.total_pnl) },
    { label: 'Avg P&L / trade', value: `₹${fmt(summary.avg_pnl)}`, sub: null,            color: pnlColor(summary.avg_pnl) },
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
      {kpis.map(({ label, value, sub, color }) => (
        <div key={label} className="bg-white rounded-xl border border-gray-100 shadow-sm px-4 py-3">
          <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">{label}</p>
          <p className={`text-xl font-bold ${color}`}>{value}</p>
          {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
        </div>
      ))}
    </div>
  );
};

/** Engine Accuracy — by_grade table */
const GradeAccuracyPanel = ({ byGrade }) => {
  const grades = GRADE_ORDER.filter(g => byGrade[g]);
  if (grades.length === 0) return null;

  const maxTrades = Math.max(...grades.map(g => byGrade[g].trades));

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
      <h3 className="text-sm font-bold text-gray-700 mb-4">
        🎯 Engine Accuracy by Grade
      </h3>
      <p className="text-xs text-gray-400 mb-3">
        Trades made from Win Probability Grade signals — did higher grades actually win more?
      </p>

      <div className="space-y-3">
        {grades.map(g => {
          const v   = byGrade[g];
          const s   = GRADE_STYLES[g] || GRADE_STYLES['Unknown'];
          return (
            <div key={g} className={`rounded-lg border ${s.border} ${s.bg} px-3 py-2.5`}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-extrabold px-2 py-0.5 rounded-full bg-white border ${s.border} ${s.text}`}>
                    {g}
                  </span>
                  <span className="text-xs text-gray-500">{v.trades} trade{v.trades !== 1 ? 's' : ''}</span>
                </div>
                <div className="flex items-center gap-2">
                  <WinRatePill winRate={v.win_rate} />
                  <span className={`text-xs font-mono font-bold ${pnlColor(v.avg_pnl)}`}>
                    avg ₹{fmt(v.avg_pnl)}
                  </span>
                </div>
              </div>
              <MiniBar value={v.trades} max={maxTrades} barClass={s.bar} height="h-1.5" />
            </div>
          );
        })}
      </div>

      <p className="mt-3 text-xs text-gray-400 italic">
        Only trades that came from the Win Probability page have a grade assigned.
        Manually opened mock trades show as "Unknown".
      </p>
    </div>
  );
};

/** P&L by underlying index */
const UnderlyingPanel = ({ byUnderlying }) => {
  const underlyings = Object.keys(byUnderlying);
  if (underlyings.length === 0) return null;

  const maxAbs = Math.max(...underlyings.map(u => Math.abs(byUnderlying[u].total_pnl)));

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
      <h3 className="text-sm font-bold text-gray-700 mb-4">📊 P&L by Underlying</h3>
      <div className="space-y-3">
        {underlyings.sort().map(u => {
          const v = byUnderlying[u];
          const isPositive = v.total_pnl >= 0;
          return (
            <div key={u} className="flex items-center gap-3">
              <span className="text-xs font-bold text-gray-700 w-24 shrink-0">{u}</span>
              <div className="flex-1">
                <div className="flex justify-between text-xs text-gray-500 mb-0.5">
                  <span>{v.trades} trade{v.trades !== 1 ? 's' : ''}</span>
                  <WinRatePill winRate={v.win_rate} />
                </div>
                <div className="flex items-center gap-1.5">
                  <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${isPositive ? 'bg-emerald-400' : 'bg-red-400'}`}
                      style={{ width: `${maxAbs > 0 ? Math.abs(v.total_pnl) / maxAbs * 100 : 0}%` }}
                    />
                  </div>
                  <span className={`text-xs font-mono font-bold w-20 text-right ${pnlColor(v.total_pnl)}`}>
                    ₹{fmt(v.total_pnl)}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

/** Best entry time of day */
const HourlyPanel = ({ byHour }) => {
  const hours = Object.keys(byHour).map(Number).sort((a, b) => a - b);
  if (hours.length === 0) return null;

  const maxAbs = Math.max(...hours.map(h => Math.abs(byHour[h].avg_pnl)));

  const fmtHour = h => {
    const suffix = h >= 12 ? 'PM' : 'AM';
    const h12 = h % 12 === 0 ? 12 : h % 12;
    return `${h12}${suffix}`;
  };

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
      <h3 className="text-sm font-bold text-gray-700 mb-4">⏰ Best Time of Day to Enter</h3>
      <p className="text-xs text-gray-400 mb-3">Average P&L per closed mock trade by entry hour</p>
      <div className="space-y-2">
        {hours.map(h => {
          const v = byHour[h];
          const isPositive = v.avg_pnl >= 0;
          return (
            <div key={h} className="flex items-center gap-3">
              <span className="text-xs font-semibold text-gray-500 w-10 shrink-0 text-right">
                {fmtHour(h)}
              </span>
              <div className="flex-1 flex items-center gap-1.5">
                <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${isPositive ? 'bg-emerald-400' : 'bg-red-400'}`}
                    style={{ width: `${maxAbs > 0 ? Math.abs(v.avg_pnl) / maxAbs * 100 : 0}%` }}
                  />
                </div>
                <span className={`text-xs font-mono font-bold w-20 text-right ${pnlColor(v.avg_pnl)}`}>
                  ₹{fmt(v.avg_pnl)}
                </span>
                <WinRatePill winRate={v.win_rate} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

/** Score vs P&L scatter — simple CSS-based dot plot */
const ScoreVsPnlPanel = ({ scoreVsPnl }) => {
  if (scoreVsPnl.length < 3) {
    return (
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
        <h3 className="text-sm font-bold text-gray-700 mb-2">🔬 Score Calibration</h3>
        <p className="text-xs text-gray-400">
          Need at least 3 closed trades from the Win Probability page to show score calibration.
        </p>
      </div>
    );
  }

  const minPnl = Math.min(...scoreVsPnl.map(d => d.pnl_pct));
  const maxPnl = Math.max(...scoreVsPnl.map(d => d.pnl_pct));
  const pnlRange = maxPnl - minPnl || 1;

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
      <h3 className="text-sm font-bold text-gray-700 mb-1">🔬 Score Calibration</h3>
      <p className="text-xs text-gray-400 mb-4">
        Each dot = one trade. X-axis = engine score at entry, Y-axis = actual P&L %. Higher scores should correlate with higher P&L.
      </p>

      {/* Scatter plot */}
      <div className="relative h-48 bg-gray-50 rounded-lg border border-gray-100 overflow-hidden">
        {/* Zero line */}
        <div
          className="absolute left-0 right-0 border-t border-dashed border-gray-300"
          style={{ top: `${((maxPnl / pnlRange) * 100)}%` }}
        />

        {scoreVsPnl.map((d, i) => {
          const x = Math.max(0, Math.min(100, ((d.score - 30) / 70) * 100)); // 30–100 → 0–100%
          const y = Math.max(0, Math.min(100, ((maxPnl - d.pnl_pct) / pnlRange) * 100));
          const isWin = d.pnl_pct > 0;
          return (
            <div
              key={i}
              className={`absolute w-2.5 h-2.5 rounded-full border border-white shadow-sm -translate-x-1/2 -translate-y-1/2
                          ${isWin ? 'bg-emerald-400' : 'bg-red-400'}`}
              style={{ left: `${x}%`, top: `${y}%` }}
              title={`Score ${d.score} → P&L ${fmt(d.pnl_pct)}%`}
            />
          );
        })}

        {/* Axis labels */}
        <div className="absolute bottom-1 left-2 text-xs text-gray-400">Score 30</div>
        <div className="absolute bottom-1 right-2 text-xs text-gray-400">Score 100</div>
        <div className="absolute top-1 left-2 text-xs text-gray-400">+P&L%</div>
        <div className="absolute bottom-6 left-2 text-xs text-gray-400">−P&L%</div>
      </div>

      <p className="text-xs text-gray-400 mt-2">
        🟢 Profitable trades &nbsp; 🔴 Loss trades &nbsp;·&nbsp; {scoreVsPnl.length} data points
      </p>
    </div>
  );
};

/** Recent closed trades table */
const RecentTradesTable = ({ recent }) => {
  if (recent.length === 0) return null;

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
      <h3 className="text-sm font-bold text-gray-700 mb-4">📋 Recent Closed Trades</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-100 text-left text-gray-400 uppercase tracking-wide">
              <th className="pb-2 pr-3">Instrument</th>
              <th className="pb-2 pr-3">Grade</th>
              <th className="pb-2 pr-3">Score</th>
              <th className="pb-2 pr-3">Entry ₹</th>
              <th className="pb-2 pr-3">Exit ₹</th>
              <th className="pb-2 pr-3">P&L ₹</th>
              <th className="pb-2 pr-3">P&L %</th>
              <th className="pb-2">Entry Time</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {recent.map(t => {
              const s = GRADE_STYLES[t.grade] || GRADE_STYLES['Unknown'];
              return (
                <tr key={t.id} className="hover:bg-gray-50">
                  <td className="py-1.5 pr-3 font-mono font-medium text-gray-800 max-w-[160px] truncate">{t.instrument}</td>
                  <td className="py-1.5 pr-3">
                    {t.grade
                      ? <span className={`px-1.5 py-0.5 rounded text-xs font-bold ${s.bg} ${s.text} border ${s.border}`}>{t.grade}</span>
                      : <span className="text-gray-300">—</span>
                    }
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-gray-500">{t.score != null ? t.score.toFixed(0) : '—'}</td>
                  <td className="py-1.5 pr-3 font-mono text-gray-600">₹{t.entry_price?.toFixed(2)}</td>
                  <td className="py-1.5 pr-3 font-mono text-gray-600">₹{t.exit_price?.toFixed(2)}</td>
                  <td className={`py-1.5 pr-3 font-mono font-bold ${pnlColor(t.pnl)}`}>₹{fmt(t.pnl)}</td>
                  <td className={`py-1.5 pr-3 font-mono font-bold ${pnlColor(t.pnl_pct)}`}>{fmt(t.pnl_pct)}%</td>
                  <td className="py-1.5 text-gray-400">
                    {t.entry_time
                      ? new Date(t.entry_time).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
                      : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// ─── Main Page ────────────────────────────────────────────────────────────────

const Performance = () => {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState('');

  const fetchAnalytics = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await performanceAPI.getAnalytics();
      setData(res.data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load analytics');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAnalytics(); }, [fetchAnalytics]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-blue-500" />
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Performance Analytics</h1>
          <p className="mt-1 text-sm text-gray-500">
            Closed mock-trade history — see whether the Win Probability engine's grades
            actually predicted profit, which underlyings work best, and your best time of day.
          </p>
        </div>
        <button
          onClick={fetchAnalytics}
          className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold transition-colors shrink-0"
        >
          ↻ Refresh
        </button>
      </div>

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {error && (
        <div className="mb-4 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
          ✗ {error}
        </div>
      )}

      {/* ── Empty state ──────────────────────────────────────────────────── */}
      {data && data.summary.total === 0 && (
        <div className="text-center py-24 text-gray-400">
          <div className="text-5xl mb-4">📊</div>
          <p className="text-lg font-medium">No closed mock trades yet</p>
          <p className="text-sm mt-1">
            Go to <strong>Mock Trading</strong> and close a few positions to start seeing performance analytics.
          </p>
          <p className="text-xs mt-2 text-gray-300">
            For the best insights, open mock trades from the <strong>Win Probability</strong> page using the 🧪 Mock Buy button.
          </p>
        </div>
      )}

      {/* ── Dashboard ────────────────────────────────────────────────────── */}
      {data && data.summary.total > 0 && (
        <>
          {/* KPI strip */}
          <SummaryStrip summary={data.summary} />

          {/* 2-col grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
            <GradeAccuracyPanel byGrade={data.by_grade} />
            <UnderlyingPanel byUnderlying={data.by_underlying} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
            <HourlyPanel byHour={data.by_hour} />
            <ScoreVsPnlPanel scoreVsPnl={data.score_vs_pnl} />
          </div>

          <RecentTradesTable recent={data.recent} />

          {/* Footer note */}
          <div className="mt-6 px-4 py-3 rounded-lg bg-gray-50 border border-gray-200 text-xs text-gray-500 leading-relaxed">
            <strong>How to read this:</strong> Trades opened via the 🧪 Mock Buy button on the Win Probability page
            carry a grade and score. Trades opened manually on the Mock Trading page show grade "Unknown".
            The engine is calibrated when Grade A+ trades consistently show a higher win rate than Grade C/D trades.
          </div>
        </>
      )}
    </div>
  );
};

export default Performance;
