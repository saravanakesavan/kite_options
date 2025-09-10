"""
Options Trading App Backend
FastAPI application for automated options trading with Zerodha Kite API
"""

from fastapi import FastAPI, HTTPException, Depends, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
import uvicorn
import os
from datetime import datetime, timedelta
import logging
import asyncio

# Local imports
from database import get_db, create_tables
from auth import AuthService, get_current_active_user
from models import User as UserModel, Order as OrderModel, TradingStrategy as StrategyModel
from dotenv import load_dotenv

# Import trading engine with error handling
try:
    from trading_engine import TradingEngine
except ImportError as e:
    logger.warning(f"Trading engine import failed: {e}. Some features may be limited.")

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Options Trading App",
    description="Automated call options trading system with ₹10,000 cap per trade",
    version="1.0.0"
)

# CORS middleware to allow frontend connections
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    create_tables()
    logger.info("Database tables created/verified")
    
    # Start automated trading in background
    # asyncio.create_task(start_trading_engine())

# Pydantic models for API
class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime

class OrderCreate(BaseModel):
    instrument: str
    quantity: int
    price: float
    order_type: str

class OrderResponse(BaseModel):
    id: int
    instrument: str
    quantity: int
    price: float
    order_type: str
    status: str
    created_at: datetime
    profit_loss: Optional[float] = None

class StrategyCreate(BaseModel):
    name: str
    strategy_type: str
    instrument: str
    max_investment: float = 10000.0
    profit_target_min: float = 300.0
    profit_target_max: float = 500.0
    use_rsi: bool = True
    rsi_oversold: int = 30
    rsi_overbought: int = 70
    use_macd: bool = True

class StrategyResponse(BaseModel):
    id: int
    name: str
    strategy_type: str
    instrument: str
    max_investment: float
    profit_target_min: float
    profit_target_max: float
    is_active: bool
    created_at: datetime

# async def start_trading_engine():
#     """Start the automated trading engine"""
#     try:
#         from trading_engine import run_automated_trading
#         await run_automated_trading()
#     except Exception as e:
#         logger.error(f"Trading engine error: {str(e)}")

# Health check endpoint
@app.get("/")
async def root():
    return {"message": "Options Trading App API", "status": "running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now()}

# Authentication endpoints
@app.post("/auth/register", response_model=dict)
async def register(user: UserCreate, db: Session = Depends(get_db)):
    """Register a new user"""
    try:
        new_user = AuthService.create_user(
            db=db,
            username=user.username,
            email=user.email,
            password=user.password
        )
        logger.info(f"User registered successfully: {user.username}")
        return {
            "message": "User registered successfully",
            "user_id": new_user.id,
            "username": new_user.username
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        raise HTTPException(status_code=400, detail="Registration failed")

@app.post("/auth/login", response_model=Token)
async def login(user: UserLogin, db: Session = Depends(get_db)):
    """Authenticate user and return access token"""
    try:
        authenticated_user = AuthService.authenticate_user(
            db=db,
            username=user.username,
            password=user.password
        )
        
        if not authenticated_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password"
            )
        
        access_token = AuthService.create_access_token(
            data={"sub": authenticated_user.username}
        )
        
        logger.info(f"User logged in successfully: {user.username}")
        return {
            "access_token": access_token,
            "token_type": "bearer"
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed")

@app.get("/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user: UserModel = Depends(get_current_active_user)):
    """Get current user information"""
    return current_user

# Trading endpoints
@app.get("/instruments")
async def get_instruments():
    """Get available trading instruments"""
    # TODO: Fetch from Kite API
    sample_instruments = [
        {"symbol": "NIFTY24DEC24000CE", "name": "NIFTY 24DEC 24000 CE", "current_price": 150.0},
        {"symbol": "BANKNIFTY24DEC51000CE", "name": "BANKNIFTY 24DEC 51000 CE", "current_price": 200.0},
        {"symbol": "RELIANCE24DEC3000CE", "name": "RELIANCE 24DEC 3000 CE", "current_price": 25.0}
    ]
    return {"instruments": sample_instruments}

@app.get("/orders", response_model=List[OrderResponse])
async def get_orders(
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get user's trading orders"""
    orders = db.query(OrderModel).filter(OrderModel.user_id == current_user.id).all()
    return orders

@app.post("/orders")
async def place_order(
    order: OrderCreate,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Place a new trading order"""
    try:
        # Validate investment cap
        order_value = order.price * order.quantity
        if order_value > 10000:
            raise HTTPException(
                status_code=400, 
                detail=f"Order value ₹{order_value:.2f} exceeds ₹10,000 limit"
            )
        
        # Create order in database
        new_order = OrderModel(
            user_id=current_user.id,
            instrument=order.instrument,
            quantity=order.quantity,
            price=order.price,
            order_type=order.order_type,
            status='PENDING',
            entry_price=order.price if order.order_type == 'BUY' else None
        )
        
        db.add(new_order)
        db.commit()
        db.refresh(new_order)
        
        logger.info(f"Order placed: {order.instrument} - {order.quantity} @ ₹{order.price} by user {current_user.username}")
        
        return {
            "message": "Order placed successfully",
            "order_id": new_order.id,
            "status": "PENDING"
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Order placement error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=400, detail="Order placement failed")

@app.get("/strategies", response_model=List[StrategyResponse])
async def get_strategies(
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get user's trading strategies"""
    strategies = db.query(StrategyModel).filter(StrategyModel.user_id == current_user.id).all()
    return strategies

@app.post("/strategies")
async def create_strategy(
    strategy: StrategyCreate,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Create a new trading strategy"""
    try:
        new_strategy = StrategyModel(
            user_id=current_user.id,
            name=strategy.name,
            strategy_type=strategy.strategy_type,
            instrument=strategy.instrument,
            max_investment=strategy.max_investment,
            profit_target_min=strategy.profit_target_min,
            profit_target_max=strategy.profit_target_max,
            use_rsi=strategy.use_rsi,
            rsi_oversold=strategy.rsi_oversold,
            rsi_overbought=strategy.rsi_overbought,
            use_macd=strategy.use_macd
        )
        
        db.add(new_strategy)
        db.commit()
        db.refresh(new_strategy)
        
        logger.info(f"Strategy created: {strategy.name} by user {current_user.username}")
        
        return {
            "message": "Strategy created successfully",
            "strategy_id": new_strategy.id,
            "strategy": new_strategy
        }
    except Exception as e:
        logger.error(f"Strategy creation error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=400, detail="Strategy creation failed")

@app.post("/trading/start")
async def start_trading(
    background_tasks: BackgroundTasks,
    current_user: UserModel = Depends(get_current_active_user)
):
    """Start automated trading for user strategies"""
    try:
        # Add background task to run trading engine
        background_tasks.add_task(run_user_trading, current_user.id)
        
        return {
            "message": "Automated trading started",
            "user_id": current_user.id
        }
    except Exception as e:
        logger.error(f"Error starting trading: {str(e)}")
        raise HTTPException(status_code=400, detail="Failed to start trading")

async def run_user_trading(user_id: int):
    """Run trading for specific user"""
    try:
        # Check if TradingEngine is available
        if 'TradingEngine' not in globals():
            logger.warning("TradingEngine not available")
            return
            
        engine = TradingEngine()
        # This would run the trading logic for the specific user
        logger.info(f"Running trading for user {user_id}")
    except Exception as e:
        logger.error(f"Trading error for user {user_id}: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)