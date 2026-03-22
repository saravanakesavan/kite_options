import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { kiteAPI } from '../services/api';
import { useAuth } from '../services/AuthContext';

const KiteCallback = () => {
  const [status, setStatus] = useState('linking'); // 'linking' | 'success' | 'error'
  const [errorMsg, setErrorMsg] = useState('');
  const navigate = useNavigate();
  const { user, refreshUser } = useAuth();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestToken =
      params.get('request_token') || sessionStorage.getItem('kite_pending_token');

    if (!requestToken) {
      setStatus('error');
      setErrorMsg('No request token found in URL.');
      return;
    }

    // If not logged in, stash the token and redirect to login first
    if (!user) {
      sessionStorage.setItem('kite_pending_token', requestToken);
      navigate('/login?kite_redirect=1', { replace: true });
      return;
    }

    // Logged in — clear the stash and exchange the token
    sessionStorage.removeItem('kite_pending_token');

    kiteAPI.exchangeToken(requestToken)
      .then(async () => {
        await refreshUser(); // update user.access_token in context
        setStatus('success');
        setTimeout(() => navigate('/dashboard'), 1500);
      })
      .catch((err) => {
        setStatus('error');
        setErrorMsg(err.response?.data?.detail || 'Failed to link Kite account.');
      });
  }, [user]); // re-run when user becomes available after login redirect

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center">
      <div className="bg-white shadow rounded-lg p-8 max-w-md w-full text-center">
        {status === 'linking' && (
          <>
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
            <h2 className="text-lg font-semibold text-gray-800">Linking your Kite account…</h2>
            <p className="text-sm text-gray-500 mt-1">This only takes a moment.</p>
          </>
        )}
        {status === 'success' && (
          <>
            <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-6 h-6 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h2 className="text-lg font-semibold text-gray-800">Kite account linked!</h2>
            <p className="text-sm text-gray-500 mt-1">Redirecting to dashboard…</p>
          </>
        )}
        {status === 'error' && (
          <>
            <div className="w-12 h-12 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-6 h-6 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </div>
            <h2 className="text-lg font-semibold text-gray-800">Linking failed</h2>
            <p className="text-sm text-red-500 mt-1">{errorMsg}</p>
            <button
              onClick={() => navigate('/dashboard')}
              className="mt-4 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium"
            >
              Back to Dashboard
            </button>
          </>
        )}
      </div>
    </div>
  );
};

export default KiteCallback;
