import React, { useState, useEffect } from 'react';
import { tradingAPI } from '../services/api';

const Strategies = () => {
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreateStrategy, setShowCreateStrategy] = useState(false);
  const [instruments, setInstruments] = useState([]);
  const [strategyForm, setStrategyForm] = useState({
    name: '',
    strategy_type: 'MANUAL',
    instrument: '',
    max_investment: 10000,
    profit_target_min: 300,
    profit_target_max: 500,
    use_rsi: true,
    rsi_oversold: 30,
    rsi_overbought: 70,
    use_macd: true
  });

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const [strategiesRes, instrumentsRes] = await Promise.all([
        tradingAPI.getStrategies(),
        tradingAPI.getInstruments()
      ]);
      
      setStrategies(strategiesRes.data || []);
      setInstruments(instrumentsRes.data.instruments || []);
    } catch (error) {
      console.error('Error fetching data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateStrategy = async (e) => {
    e.preventDefault();
    
    try {
      await tradingAPI.createStrategy(strategyForm);
      
      // Reset form and refresh strategies
      setStrategyForm({
        name: '',
        strategy_type: 'MANUAL',
        instrument: '',
        max_investment: 10000,
        profit_target_min: 300,
        profit_target_max: 500,
        use_rsi: true,
        rsi_oversold: 30,
        rsi_overbought: 70,
        use_macd: true
      });
      setShowCreateStrategy(false);
      fetchData();
      
      alert('Strategy created successfully!');
    } catch (error) {
      alert('Failed to create strategy: ' + (error.response?.data?.detail || error.message));
    }
  };

  const formatDate = (dateString) => {
    return new Date(dateString).toLocaleDateString();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-32 w-32 border-b-2 border-blue-500"></div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="mb-8 flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Trading Strategies</h1>
          <p className="mt-2 text-gray-600">Create and manage your automated trading strategies</p>
        </div>
        <button
          onClick={() => setShowCreateStrategy(true)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium"
        >
          Create New Strategy
        </button>
      </div>

      {/* Create Strategy Modal */}
      {showCreateStrategy && (
        <div className="fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full z-50">
          <div className="relative top-10 mx-auto p-5 border w-full max-w-2xl shadow-lg rounded-md bg-white">
            <div className="mt-3">
              <h3 className="text-lg font-medium text-gray-900 mb-4">Create New Trading Strategy</h3>
              <form onSubmit={handleCreateStrategy} className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700">Strategy Name</label>
                    <input
                      type="text"
                      value={strategyForm.name}
                      onChange={(e) => setStrategyForm({...strategyForm, name: e.target.value})}
                      required
                      className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      placeholder="Enter strategy name"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700">Strategy Type</label>
                    <select
                      value={strategyForm.strategy_type}
                      onChange={(e) => setStrategyForm({...strategyForm, strategy_type: e.target.value})}
                      className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                    >
                      <option value="MANUAL">Manual</option>
                      <option value="AUTOMATED">Automated</option>
                    </select>
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700">Instrument</label>
                  <select
                    value={strategyForm.instrument}
                    onChange={(e) => setStrategyForm({...strategyForm, instrument: e.target.value})}
                    required
                    className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                  >
                    <option value="">Select Instrument</option>
                    {instruments.map((inst, index) => (
                      <option key={index} value={inst.tradingsymbol}>
                        {inst.tradingsymbol} — ₹{inst.last_price ? inst.last_price.toFixed(2) : '0.00'} | Strike ₹{inst.strike?.toLocaleString('en-IN')}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700">Max Investment (₹)</label>
                    <input
                      type="number"
                      value={strategyForm.max_investment}
                      onChange={(e) => setStrategyForm({...strategyForm, max_investment: parseFloat(e.target.value)})}
                      required
                      max="10000"
                      className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700">Min Profit Target (₹)</label>
                    <input
                      type="number"
                      value={strategyForm.profit_target_min}
                      onChange={(e) => setStrategyForm({...strategyForm, profit_target_min: parseFloat(e.target.value)})}
                      required
                      className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700">Max Profit Target (₹)</label>
                    <input
                      type="number"
                      value={strategyForm.profit_target_max}
                      onChange={(e) => setStrategyForm({...strategyForm, profit_target_max: parseFloat(e.target.value)})}
                      required
                      className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                </div>

                {strategyForm.strategy_type === 'AUTOMATED' && (
                  <div className="border-t pt-4">
                    <h4 className="text-md font-medium text-gray-900 mb-3">Technical Indicators</h4>
                    
                    <div className="space-y-3">
                      <div className="flex items-center space-x-4">
                        <label className="flex items-center">
                          <input
                            type="checkbox"
                            checked={strategyForm.use_rsi}
                            onChange={(e) => setStrategyForm({...strategyForm, use_rsi: e.target.checked})}
                            className="rounded border-gray-300 text-blue-600 shadow-sm focus:border-blue-300 focus:ring focus:ring-blue-200 focus:ring-opacity-50"
                          />
                          <span className="ml-2 text-sm text-gray-700">Use RSI</span>
                        </label>
                        
                        {strategyForm.use_rsi && (
                          <>
                            <div>
                              <label className="text-xs text-gray-500">Oversold</label>
                              <input
                                type="number"
                                value={strategyForm.rsi_oversold}
                                onChange={(e) => setStrategyForm({...strategyForm, rsi_oversold: parseInt(e.target.value)})}
                                min="1"
                                max="49"
                                className="w-16 px-2 py-1 text-sm border border-gray-300 rounded"
                              />
                            </div>
                            <div>
                              <label className="text-xs text-gray-500">Overbought</label>
                              <input
                                type="number"
                                value={strategyForm.rsi_overbought}
                                onChange={(e) => setStrategyForm({...strategyForm, rsi_overbought: parseInt(e.target.value)})}
                                min="51"
                                max="99"
                                className="w-16 px-2 py-1 text-sm border border-gray-300 rounded"
                              />
                            </div>
                          </>
                        )}
                      </div>
                      
                      <div className="flex items-center">
                        <label className="flex items-center">
                          <input
                            type="checkbox"
                            checked={strategyForm.use_macd}
                            onChange={(e) => setStrategyForm({...strategyForm, use_macd: e.target.checked})}
                            className="rounded border-gray-300 text-blue-600 shadow-sm focus:border-blue-300 focus:ring focus:ring-blue-200 focus:ring-opacity-50"
                          />
                          <span className="ml-2 text-sm text-gray-700">Use MACD</span>
                        </label>
                      </div>
                    </div>
                  </div>
                )}

                <div className="flex space-x-3 pt-4">
                  <button
                    type="submit"
                    className="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium"
                  >
                    Create Strategy
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowCreateStrategy(false)}
                    className="flex-1 bg-gray-300 hover:bg-gray-400 text-gray-700 px-4 py-2 rounded-md text-sm font-medium"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* Strategies Grid */}
      {strategies.length === 0 ? (
        <div className="bg-white shadow rounded-lg p-6 text-center">
          <p className="text-gray-500">No strategies created yet. Create your first strategy to start automated trading.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {strategies.map((strategy) => (
            <div key={strategy.id} className="bg-white shadow rounded-lg p-6">
              <div className="flex justify-between items-start mb-4">
                <h3 className="text-lg font-medium text-gray-900">{strategy.name}</h3>
                <span className={`inline-flex px-2 py-1 text-xs font-semibold rounded-full ${
                  strategy.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                }`}>
                  {strategy.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>

              <div className="space-y-3">
                <div>
                  <span className="text-sm font-medium text-gray-500">Type:</span>
                  <span className={`ml-2 inline-flex px-2 py-1 text-xs font-semibold rounded-full ${
                    strategy.strategy_type === 'AUTOMATED' ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-800'
                  }`}>
                    {strategy.strategy_type}
                  </span>
                </div>

                <div>
                  <span className="text-sm font-medium text-gray-500">Instrument:</span>
                  <span className="ml-2 text-sm text-gray-900">{strategy.instrument}</span>
                </div>

                <div>
                  <span className="text-sm font-medium text-gray-500">Max Investment:</span>
                  <span className="ml-2 text-sm text-gray-900">₹{strategy.max_investment}</span>
                </div>

                <div>
                  <span className="text-sm font-medium text-gray-500">Profit Targets:</span>
                  <span className="ml-2 text-sm text-gray-900">
                    ₹{strategy.profit_target_min} - ₹{strategy.profit_target_max}
                  </span>
                </div>

                <div>
                  <span className="text-sm font-medium text-gray-500">Created:</span>
                  <span className="ml-2 text-sm text-gray-900">{formatDate(strategy.created_at)}</span>
                </div>
              </div>

              <div className="mt-6 flex space-x-3">
                <button className="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-3 py-2 rounded-md text-sm font-medium">
                  {strategy.is_active ? 'Pause' : 'Activate'}
                </button>
                <button className="flex-1 bg-gray-300 hover:bg-gray-400 text-gray-700 px-3 py-2 rounded-md text-sm font-medium">
                  Edit
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default Strategies;