import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../services/AuthContext';

const Navigation = () => {
  const { user, logout, alerts, dismissAlert } = useAuth();
  const location = useLocation();

  const isActive = (path) => location.pathname === path;

  // Actionable (exit/profit/SL) alerts that haven't been dismissed
  const actionableAlerts = alerts.filter(
    a => a.type === 'ALERT' && (
      a.alert_type === 'PROFIT_TARGET' ||
      a.alert_type === 'STOP_LOSS' ||
      a.alert_type === 'FORCE_EXIT' ||
      a.alert_type === 'EXTENDED_PROFIT'
    )
  );
  const badgeCount = actionableAlerts.length;

  return (
    <nav className="bg-blue-600 shadow-lg">
      <div className="max-w-7xl mx-auto px-4">
        <div className="flex justify-between h-16">
          <div className="flex items-center">
            <Link to="/dashboard" className="text-white text-xl font-bold">
              Options Trading App
            </Link>
          </div>

          <div className="flex items-center space-x-4">
            <Link
              to="/dashboard"
              className={`px-3 py-2 rounded-md text-sm font-medium ${
                isActive('/dashboard')
                  ? 'bg-blue-700 text-white'
                  : 'text-blue-100 hover:bg-blue-500 hover:text-white'
              }`}
            >
              Dashboard
            </Link>

            <Link
              to="/orders"
              className={`px-3 py-2 rounded-md text-sm font-medium ${
                isActive('/orders')
                  ? 'bg-blue-700 text-white'
                  : 'text-blue-100 hover:bg-blue-500 hover:text-white'
              }`}
            >
              Orders
            </Link>

            <Link
              to="/strategies"
              className={`px-3 py-2 rounded-md text-sm font-medium flex items-center gap-1 ${
                isActive('/strategies')
                  ? 'bg-blue-700 text-white'
                  : 'text-blue-100 hover:bg-blue-500 hover:text-white'
              }`}
            >
              📈 Performance
            </Link>

            <Link
              to="/win-probability"
              className={`px-3 py-2 rounded-md text-sm font-medium flex items-center gap-1 ${
                isActive('/win-probability')
                  ? 'bg-blue-700 text-white'
                  : 'text-blue-100 hover:bg-blue-500 hover:text-white'
              }`}
            >
              🎯 Win Probability
            </Link>

            <Link
              to="/mock-trading"
              className={`px-3 py-2 rounded-md text-sm font-medium flex items-center gap-1 ${
                isActive('/mock-trading')
                  ? 'bg-blue-700 text-white'
                  : 'text-blue-100 hover:bg-blue-500 hover:text-white'
              }`}
            >
              🧪 Mock Trading
            </Link>

            {/* Alert bell */}
            {user && (
              <div className="relative">
                <button
                  className="relative p-2 rounded-full text-blue-100 hover:bg-blue-500 transition-colors"
                  title={badgeCount > 0 ? `${badgeCount} active alert${badgeCount > 1 ? 's' : ''}` : 'No new alerts'}
                  onClick={() => {
                    // Dismiss all watching alerts on bell click (keep actionable ones)
                    alerts
                      .filter(a => a.type === 'WATCHING')
                      .forEach(a => dismissAlert(a.id));
                  }}
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6 6 0 10-12 0v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                  </svg>
                  {badgeCount > 0 && (
                    <span className="absolute top-0.5 right-0.5 h-4 w-4 rounded-full bg-red-500 text-white text-xs font-bold flex items-center justify-center animate-pulse">
                      {badgeCount > 9 ? '9+' : badgeCount}
                    </span>
                  )}
                </button>
              </div>
            )}

            <div className="flex items-center space-x-3">
              <span className="text-blue-100 text-sm">
                Welcome, {user?.username}
              </span>
              <button
                onClick={logout}
                className="bg-blue-700 hover:bg-blue-800 text-white px-3 py-2 rounded-md text-sm font-medium"
              >
                Logout
              </button>
            </div>
          </div>
        </div>
      </div>
    </nav>
  );
};

export default Navigation;
