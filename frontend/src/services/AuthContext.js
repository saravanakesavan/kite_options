import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react';
import { authAPI } from './api';
import { AlertWebSocket } from './websocket';

const AuthContext = createContext();

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

let _alertIdCounter = 1;

export const AuthProvider = ({ children }) => {
  const [user,    setUser]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [alerts,  setAlerts]  = useState([]);  // real-time alert queue
  const wsRef = useRef(null);

  // ── Alert helpers ──────────────────────────────────────────────────────────

  const addAlert = useCallback((payload) => {
    setAlerts(prev => [
      ...prev,
      { ...payload, id: _alertIdCounter++ },
    ]);
  }, []);

  const dismissAlert = useCallback((id) => {
    setAlerts(prev => prev.filter(a => a.id !== id));
  }, []);

  // ── WebSocket lifecycle ────────────────────────────────────────────────────

  const connectWS = useCallback((token) => {
    if (wsRef.current) return; // already connected
    const ws = new AlertWebSocket(token, addAlert);
    ws.connect();
    wsRef.current = ws;
  }, [addAlert]);

  const disconnectWS = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.disconnect();
      wsRef.current = null;
    }
  }, []);

  // ── Auth lifecycle ─────────────────────────────────────────────────────────

  useEffect(() => {
    checkAuthStatus();
    return () => disconnectWS(); // cleanup on unmount
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const checkAuthStatus = async () => {
    try {
      const token = localStorage.getItem('token');
      if (token) {
        const response = await authAPI.getCurrentUser();
        setUser(response.data);
        connectWS(token);
      }
    } catch (error) {
      console.error('Auth check failed:', error);
      localStorage.removeItem('token');
    } finally {
      setLoading(false);
    }
  };

  const login = async (username, password) => {
    try {
      const response = await authAPI.login(username, password);
      const { access_token } = response.data;
      localStorage.setItem('token', access_token);

      const userResponse = await authAPI.getCurrentUser();
      setUser(userResponse.data);
      connectWS(access_token);

      return { success: true };
    } catch (error) {
      return {
        success: false,
        error: error.response?.data?.detail || 'Login failed',
      };
    }
  };

  const register = async (username, email, password) => {
    try {
      await authAPI.register(username, email, password);
      return { success: true };
    } catch (error) {
      return {
        success: false,
        error: error.response?.data?.detail || 'Registration failed',
      };
    }
  };

  const logout = () => {
    localStorage.removeItem('token');
    setUser(null);
    setAlerts([]);
    disconnectWS();
  };

  const refreshUser = async () => {
    try {
      const response = await authAPI.getCurrentUser();
      setUser(response.data);
    } catch (error) {
      console.error('Failed to refresh user:', error);
    }
  };

  const value = {
    user,
    login,
    register,
    logout,
    refreshUser,
    loading,
    // Alert system
    alerts,
    addAlert,
    dismissAlert,
    unreadAlertCount: alerts.filter(a => a.type === 'ALERT' || a.actioned).length,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};
