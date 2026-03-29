import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Login from './pages/Login';
import Register from './pages/Register';
import Dashboard from './pages/Dashboard';
import Orders from './pages/Orders';
import Performance from './pages/Strategies';
import KiteCallback from './pages/KiteCallback';
import WinProbability from './pages/WinProbability';
import MockTrading from './pages/MockTrading';
import Navigation from './components/Navigation';
import AlertToast from './components/AlertToast';
import { AuthProvider, useAuth } from './services/AuthContext';

function App() {
  return (
    <AuthProvider>
      <Router>
        <div className="min-h-screen bg-gray-100">
          <AppContent />
        </div>
      </Router>
    </AuthProvider>
  );
}

function AppContent() {
  const { user, loading, alerts, dismissAlert } = useAuth();

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-32 w-32 border-b-2 border-blue-500"></div>
      </div>
    );
  }

  return (
    <>
      {user && <Navigation />}
      <Routes>
        <Route
          path="/login"
          element={!user ? <Login /> : <Navigate to="/dashboard" />}
        />
        <Route
          path="/register"
          element={!user ? <Register /> : <Navigate to="/dashboard" />}
        />
        <Route
          path="/dashboard"
          element={user ? <Dashboard /> : <Navigate to="/login" />}
        />
        <Route
          path="/orders"
          element={user ? <Orders /> : <Navigate to="/login" />}
        />
        <Route
          path="/strategies"
          element={user ? <Performance /> : <Navigate to="/login" />}
        />
        <Route
          path="/win-probability"
          element={user ? <WinProbability /> : <Navigate to="/login" />}
        />
        <Route
          path="/mock-trading"
          element={user ? <MockTrading /> : <Navigate to="/login" />}
        />
        <Route
          path="/kite-callback"
          element={<KiteCallback />}
        />
        <Route
          path="/"
          element={<Navigate to={user ? "/dashboard" : "/login"} />}
        />
      </Routes>

      {/* Real-time alert toasts — shown on every page */}
      {user && <AlertToast alerts={alerts} onDismiss={dismissAlert} />}
    </>
  );
}

export default App;
