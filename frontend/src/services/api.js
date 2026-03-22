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
    if (error.response?.status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/login';
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
};

// Trading API
export const tradingAPI = {
  getInstruments: () => 
    api.get('/instruments'),
  
  getOrders: () => 
    api.get('/orders'),
  
  placeOrder: (orderData) => 
    api.post('/orders', orderData),
  
  getStrategies: () => 
    api.get('/strategies'),
  
  createStrategy: (strategyData) => 
    api.post('/strategies', strategyData),
  
  startTrading: () => 
    api.post('/trading/start'),
};

export default api;