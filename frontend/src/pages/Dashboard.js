import React, { useState, useEffect } from 'react';
import { tradingAPI } from '../services/api';

const Dashboard = () => {
  const [instruments, setInstruments] = useState([]);
  const [orders, setOrders] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState({
    totalOrders: 0,
    activeStrategies: 0,
    totalPnL: 0,
    todaysPnL: 0
  });

  useEffect(() => {
    fetchDashboardData();
  }, []);

  const fetchDashboardData = async () => {
    try {
      const [instrumentsRes, ordersRes, strategiesRes] = await Promise.all([
        tradingAPI.getInstruments(),
        tradingAPI.getOrders(),
        tradingAPI.getStrategies()
      ]);

      setInstruments(instrumentsRes.data.instruments || []);
      setOrders(ordersRes.data || []);
      setStrategies(strategiesRes.data || []);

      // Calculate stats
      const totalPnL = ordersRes.data.reduce((sum, order) => sum + (order.profit_loss || 0), 0);
      const activeStrategies = strategiesRes.data.filter(s => s.is_active).length;

      setStats({
        totalOrders: ordersRes.data.length,
        activeStrategies,
        totalPnL,
        todaysPnL: totalPnL // Simplified for demo
      });

    } catch (error) {
      console.error('Error fetching dashboard data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleStartTrading = async () => {
    try {
      await tradingAPI.startTrading();
      alert('Automated trading started successfully!');
    } catch (error) {
      alert('Failed to start trading: ' + (error.response?.data?.detail || error.message));
    }
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
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">Trading Dashboard</h1>
        <p className="mt-2 text-gray-600">Monitor your options trading performance</p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
        <div className="bg-white overflow-hidden shadow rounded-lg">
          <div className="p-5">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <div className="w-8 h-8 bg-blue-500 rounded-md flex items-center justify-center">
                  <span className="text-white font-bold">O</span>
                </div>
              </div>
              <div className="ml-5 w-0 flex-1">
                <dl>
                  <dt className="text-sm font-medium text-gray-500 truncate">Total Orders</dt>
                  <dd className="text-lg font-medium text-gray-900">{stats.totalOrders}</dd>
                </dl>
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white overflow-hidden shadow rounded-lg">
          <div className="p-5">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <div className="w-8 h-8 bg-green-500 rounded-md flex items-center justify-center">
                  <span className="text-white font-bold">S</span>
                </div>
              </div>
              <div className="ml-5 w-0 flex-1">
                <dl>
                  <dt className="text-sm font-medium text-gray-500 truncate">Active Strategies</dt>
                  <dd className="text-lg font-medium text-gray-900">{stats.activeStrategies}</dd>
                </dl>
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white overflow-hidden shadow rounded-lg">
          <div className="p-5">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <div className={`w-8 h-8 ${stats.totalPnL >= 0 ? 'bg-green-500' : 'bg-red-500'} rounded-md flex items-center justify-center`}>
                  <span className="text-white font-bold">₹</span>
                </div>
              </div>
              <div className="ml-5 w-0 flex-1">
                <dl>
                  <dt className="text-sm font-medium text-gray-500 truncate">Total P&L</dt>
                  <dd className={`text-lg font-medium ${stats.totalPnL >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    ₹{stats.totalPnL.toFixed(2)}
                  </dd>
                </dl>
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white overflow-hidden shadow rounded-lg">
          <div className="p-5">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <div className={`w-8 h-8 ${stats.todaysPnL >= 0 ? 'bg-blue-500' : 'bg-red-500'} rounded-md flex items-center justify-center`}>
                  <span className="text-white font-bold">T</span>
                </div>
              </div>
              <div className="ml-5 w-0 flex-1">
                <dl>
                  <dt className="text-sm font-medium text-gray-500 truncate">Today's P&L</dt>
                  <dd className={`text-lg font-medium ${stats.todaysPnL >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    ₹{stats.todaysPnL.toFixed(2)}
                  </dd>
                </dl>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="bg-white shadow rounded-lg p-6 mb-8">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Quick Actions</h2>
        <div className="flex space-x-4">
          <button
            onClick={handleStartTrading}
            className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-md text-sm font-medium"
          >
            Start Automated Trading
          </button>
          <button
            onClick={() => window.location.href = '/strategies'}
            className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium"
          >
            Create Strategy
          </button>
          <button
            onClick={() => window.location.href = '/orders'}
            className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded-md text-sm font-medium"
          >
            View All Orders
          </button>
        </div>
      </div>

      {/* Available Instruments */}
      <div className="bg-white shadow rounded-lg p-6 mb-8">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Available Instruments</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {instruments.map((instrument, index) => (
            <div key={index} className="border border-gray-200 rounded-lg p-4">
              <h3 className="font-medium text-gray-900">{instrument.name}</h3>
              <p className="text-sm text-gray-500">{instrument.symbol}</p>
              <p className="text-lg font-semibold text-green-600">₹{instrument.current_price}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Recent Orders */}
      <div className="bg-white shadow rounded-lg p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Recent Orders</h2>
        {orders.length === 0 ? (
          <p className="text-gray-500">No orders yet. Create a strategy to start trading.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Instrument
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Type
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Quantity
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Price
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    P&L
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {orders.slice(0, 5).map((order) => (
                  <tr key={order.id}>
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                      {order.instrument}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {order.order_type}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {order.quantity}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      ₹{order.price}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`inline-flex px-2 py-1 text-xs font-semibold rounded-full ${
                        order.status === 'EXECUTED' ? 'bg-green-100 text-green-800' : 
                        order.status === 'PENDING' ? 'bg-yellow-100 text-yellow-800' : 
                        'bg-red-100 text-red-800'
                      }`}>
                        {order.status}
                      </span>
                    </td>
                    <td className={`px-6 py-4 whitespace-nowrap text-sm ${
                      (order.profit_loss || 0) >= 0 ? 'text-green-600' : 'text-red-600'
                    }`}>
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