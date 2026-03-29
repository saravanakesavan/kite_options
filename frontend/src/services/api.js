import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

// Create axios instance
const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle responses and errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || '';

    // JWT expired / not logged in → redirect to login
    if (status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/login';
      return Promise.reject(error);
    }

    // Kite session expired → redirect to dashboard for re-link
    // Backend sets KITE_TOKEN_EXPIRED prefix so we can distinguish this
    // from other 403s (e.g. "Kite account not linked").
    if (status === 403 && detail.includes('KITE_TOKEN_EXPIRED')) {
      // Avoid redirect loops if already on dashboard
      if (!window.location.pathname.includes('/dashboard')) {
        window.location.href = '/dashboard?kite_expired=1';
      }
    }

    // Kite market data permission issue (subscription/plan problem — re-link won't fix it)
    // Backend sets KITE_NO_MARKET_DATA prefix to distinguish from token expiry.
    if (status === 403 && detail.includes('KITE_NO_MARKET_DATA')) {
      if (!window.location.pathname.includes('/dashboard')) {
        window.location.href = '/dashboard?kite_no_market_data=1';
      }
    }

    return Promise.reject(error);
  }
);

// Auth API
export const authAPI = {
  login: (username, password) =>
    api.post('/auth/login', { username, password }),

  register: (username, email, password) =>
    api.post('/auth/register', { username, email, password }),

  getCurrentUser: () =>
    api.get('/auth/me'),
};

// Kite OAuth API
export const kiteAPI = {
  getLoginUrl: () =>
    api.get('/auth/kite/login'),

  exchangeToken: (request_token) =>
    api.post('/auth/kite/token', { request_token }),
};

// Analysis API
export const analysisAPI = {
  getMargins: () =>
    api.get('/margins'),

  analyzeInstrument: (tradingsymbol, instrument_token) =>
    api.get(`/signal/analyze/${tradingsymbol}`, { params: { instrument_token } }),

  getDashboardSummary: () =>
    api.get('/dashboard/summary'),

  syncOrdersFromKite: () =>
    api.post('/sync/orders'),
};

// Win Probability Ranking API
export const rankAPI = {
  getRanking: (underlying = 'NIFTY', maxResults = 10) =>
    api.get('/rank', { params: { underlying, max_results: maxResults } }),

  // On-demand LLM analysis for a single instrument (costs 1 Anthropic API call)
  analyzeLLM: (instrument) =>
    api.post('/rank/llm-analyze', instrument),
};

// Mock Trading API
export const mockAPI = {
  // Open a new mock (paper) position — entry price fetched live from Kite
  // winScore / winGrade are optional — passed when coming from Win Probability page
  openTrade: (instrument, quantity, notes, winScore = null, winGrade = null) =>
    api.post('/mock/trades', {
      instrument,
      quantity,
      notes,
      win_probability_score: winScore,
      win_probability_grade: winGrade,
    }),

  // List all mock trades for the current user
  // statusFilter: "OPEN" | "CLOSED" | undefined (all)
  listTrades: (statusFilter) =>
    api.get('/mock/trades', { params: statusFilter ? { status_filter: statusFilter } : {} }),

  // Poll live P&L for all OPEN mock trades
  getPnL: () =>
    api.get('/mock/trades/pnl'),

  // Close (exit) a mock trade at live LTP
  closeTrade: (tradeId) =>
    api.delete(`/mock/trades/${tradeId}`),

  // Permanently delete a CLOSED mock trade record
  deleteTrade: (tradeId) =>
    api.delete(`/mock/trades/${tradeId}/delete`),
};

// Performance Analytics API
export const performanceAPI = {
  getAnalytics: () => api.get('/mock/trades/analytics'),
};

// Trading API
export const tradingAPI = {
  // underlying: 'NIFTY' | 'BANKNIFTY' | 'NIFTYNXT50' | 'MIDCPNIFTY'
  getInstruments: (underlying = 'NIFTY') =>
    api.get('/instruments', { params: { underlying } }),

  getOrders: () =>
    api.get('/orders'),

  placeOrder: (orderData) =>
    api.post('/orders', orderData),

  // Sync a single order's status from Kite (PENDING → EXECUTED/REJECTED)
  syncOrderStatus: (orderId) =>
    api.post(`/orders/${orderId}/sync-status`),

  // Exit (close) an open BUY position at market price
  exitPosition: (orderId) =>
    api.post(`/orders/${orderId}/exit`),

  getStrategies: () =>
    api.get('/strategies'),

  createStrategy: (strategyData) =>
    api.post('/strategies', strategyData),

  startTrading: () =>
    api.post('/trading/start'),
};

export default api;