# Options Trading App

A comprehensive options trading application with automated trading capabilities, focusing on call options with a ₹10,000 investment cap per trade.

## Features

### Backend (FastAPI)
- **User Authentication**: Secure registration and login system
- **Order Management**: Place, track, and manage trading orders
- **Trading Strategies**: Create manual and automated trading strategies
- **Technical Analysis**: RSI and MACD indicators for automated decision making
- **Risk Management**: ₹10,000 investment cap per trade
- **Profit Targets**: Automatic exit when profit reaches ₹300-₹500 range
- **Database**: SQLite for development, PostgreSQL ready for production

### Frontend (React)
- **User Interface**: Clean, responsive design with Tailwind CSS
- **Authentication**: Login/signup forms
- **Dashboard**: Overview of trading performance and quick actions
- **Orders Page**: View and place new orders
- **Strategies Page**: Create and manage trading strategies
- **Real-time Updates**: Live data from backend API

### Trading Engine
- **Automated Trading**: Runs every minute to check market conditions
- **Technical Indicators**: RSI (oversold/overbought) and MACD signals
- **Risk Management**: Automatic stop-loss and profit-taking
- **Strategy Execution**: Both manual selection and automated monitoring

## Installation

### Prerequisites
- Python 3.8+
- Node.js 16+
- npm or yarn

### Backend Setup

1. Navigate to the backend directory:
```bash
cd backend
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create environment file:
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. Initialize the database:
```bash
python database.py
```

6. Run the server:
```bash
python main.py
```

The API will be available at `http://localhost:8000`

### Frontend Setup

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

3. Start the development server:
```bash
npm start
```

The application will be available at `http://localhost:3000`

## API Endpoints

### Authentication
- `POST /auth/register` - Register new user
- `POST /auth/login` - User login
- `GET /auth/me` - Get current user info

### Trading
- `GET /instruments` - Get available trading instruments
- `GET /orders` - Get user's orders
- `POST /orders` - Place new order
- `GET /strategies` - Get user's strategies
- `POST /strategies` - Create new strategy
- `POST /trading/start` - Start automated trading

## Configuration

### Environment Variables (.env)

```env
# Database
DATABASE_URL=sqlite:///./trading_app.db

# Security
SECRET_KEY=your-super-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Trading Parameters
MAX_INVESTMENT_PER_TRADE=10000
PROFIT_TARGET_MIN=300
PROFIT_TARGET_MAX=500
STOP_LOSS_PERCENTAGE=20

# Kite API (for production)
KITE_API_KEY=your-kite-api-key
KITE_API_SECRET=your-kite-api-secret
```

## Trading Strategies

### Manual Strategy
- User selects a specific instrument at the start of the day
- Manual order placement within ₹10,000 limit
- Profit targets: ₹300-₹500 range

### Automated Strategy
- Continuous monitoring using RSI and MACD indicators
- Automatic order placement when conditions are favorable
- Same ₹10,000 cap and profit exit conditions
- Runs every minute during trading hours

### Technical Indicators
- **RSI**: Default oversold (30), overbought (70)
- **MACD**: Signal line crossover for entry/exit
- **Stop Loss**: 20% of investment amount

## Database Schema

### Users Table
- User credentials and profile information
- Kite API credentials (encrypted)

### Orders Table
- Order details, status, and P&L tracking
- Links to user and strategy

### Trading Strategies Table
- Strategy configuration and parameters
- Technical indicator settings

### Market Data Table
- Historical price data and calculated indicators

## Development

### Running Tests
```bash
# Backend tests
cd backend
pytest

# Frontend tests
cd frontend
npm test
```

### Code Structure

#### Backend
- `main.py` - FastAPI application and routes
- `models.py` - Database models
- `database.py` - Database configuration
- `auth.py` - Authentication utilities
- `trading_engine.py` - Automated trading logic

#### Frontend
- `src/App.js` - Main application component
- `src/pages/` - Page components (Dashboard, Orders, Strategies)
- `src/components/` - Reusable components
- `src/services/` - API calls and authentication context

## Production Deployment

1. **Database**: Switch to PostgreSQL for production
2. **Environment**: Update .env with production values
3. **Kite API**: Configure real Zerodha Kite API credentials
4. **Security**: Use proper secret keys and HTTPS
5. **Monitoring**: Add logging and error tracking

## Security Notes

- JWT tokens for authentication
- Password hashing with bcrypt
- CORS configuration for frontend access
- Input validation and sanitization
- Environment-based configuration

## Trading Disclaimer

This application is for educational and development purposes. Real trading involves financial risk. Ensure proper testing and risk management before using with actual trading accounts.

## License

MIT License