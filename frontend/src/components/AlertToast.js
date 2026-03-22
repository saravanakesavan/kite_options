/**
 * AlertToast — real-time alert notification system.
 *
 * Renders a stack of toast cards in the bottom-right corner.
 * Each card auto-dismisses after 12 seconds (actionable alerts stay until dismissed).
 *
 * Props:
 *   alerts  : array of alert objects (from AuthContext)
 *   onDismiss(id) : callback to remove an alert
 */

import React, { useEffect } from 'react';

const TYPE_CONFIG = {
  PROFIT_TARGET: {
    bg:    'bg-green-50 border-green-400',
    icon:  '🎯',
    title: 'Profit Target Hit',
    text:  'text-green-800',
    bar:   'bg-green-400',
  },
  EXTENDED_PROFIT: {
    bg:    'bg-green-50 border-green-400',
    icon:  '🚀',
    title: 'Extended Profit',
    text:  'text-green-800',
    bar:   'bg-green-400',
  },
  STOP_LOSS: {
    bg:    'bg-red-50 border-red-400',
    icon:  '🛑',
    title: 'Stop-Loss Triggered',
    text:  'text-red-800',
    bar:   'bg-red-400',
  },
  FORCE_EXIT: {
    bg:    'bg-orange-50 border-orange-400',
    icon:  '⏰',
    title: 'Force Exit — Market Closing',
    text:  'text-orange-800',
    bar:   'bg-orange-400',
  },
  WATCHING: {
    bg:    'bg-blue-50 border-blue-300',
    icon:  '👁',
    title: 'Position Update',
    text:  'text-blue-800',
    bar:   'bg-blue-300',
  },
  ALERT: {
    bg:    'bg-yellow-50 border-yellow-400',
    icon:  '⚠️',
    title: 'Alert',
    text:  'text-yellow-900',
    bar:   'bg-yellow-400',
  },
};

const AUTO_DISMISS_MS = 12_000;  // non-actionable toasts dismiss after 12 s

const Toast = ({ alert, onDismiss }) => {
  const cfg = TYPE_CONFIG[alert.alert_type || alert.type] || TYPE_CONFIG.ALERT;
  const isActionable = alert.actioned === true;

  useEffect(() => {
    if (!isActionable) {
      const t = setTimeout(() => onDismiss(alert.id), AUTO_DISMISS_MS);
      return () => clearTimeout(t);
    }
  }, [alert.id, isActionable, onDismiss]);

  return (
    <div
      className={`relative w-80 rounded-xl shadow-lg border-l-4 p-4 ${cfg.bg} animate-slide-in`}
      role="alert"
    >
      {/* Progress bar for auto-dismiss */}
      {!isActionable && (
        <div
          className={`absolute bottom-0 left-0 h-0.5 rounded-bl-xl ${cfg.bar} animate-shrink`}
          style={{ animationDuration: `${AUTO_DISMISS_MS}ms` }}
        />
      )}

      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          <span className="text-xl leading-none mt-0.5">{cfg.icon}</span>
          <div className="min-w-0">
            <p className={`font-bold text-sm ${cfg.text}`}>{cfg.title}</p>
            <p className={`text-xs font-semibold ${cfg.text} opacity-90`}>
              {alert.instrument}
            </p>

            {/* P&L badge */}
            {alert.pnl_pct != null && (
              <span
                className={`inline-block mt-1 px-2 py-0.5 rounded-full text-xs font-bold ${
                  alert.pnl_pct >= 0
                    ? 'bg-green-100 text-green-700'
                    : 'bg-red-100 text-red-700'
                }`}
              >
                {alert.pnl_pct >= 0 ? '+' : ''}{alert.pnl_pct?.toFixed(2)}%
                {alert.pnl != null && (
                  <span className="ml-1 font-normal opacity-75">
                    (₹{alert.pnl >= 0 ? '+' : ''}{alert.pnl?.toFixed(2)})
                  </span>
                )}
              </span>
            )}

            <p className={`text-xs mt-1 ${cfg.text} opacity-70 leading-snug`}>
              {alert.message}
            </p>
          </div>
        </div>

        <button
          onClick={() => onDismiss(alert.id)}
          className={`shrink-0 ${cfg.text} opacity-50 hover:opacity-100 text-base leading-none font-bold`}
        >
          ✕
        </button>
      </div>
    </div>
  );
};

const AlertToast = ({ alerts, onDismiss }) => {
  if (!alerts || alerts.length === 0) return null;

  return (
    <div
      className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 items-end"
      style={{ maxHeight: '80vh', overflowY: 'auto' }}
    >
      {/* Show newest on top */}
      {[...alerts].reverse().map((alert) => (
        <Toast key={alert.id} alert={alert} onDismiss={onDismiss} />
      ))}
    </div>
  );
};

export default AlertToast;
