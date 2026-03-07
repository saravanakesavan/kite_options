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

from kite_service import KiteService
from signal_engine import SignalEngine
from models import SignalRecord as SignalRecordModel

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
    order_type: str                         # "BUY" or "SELL"
    stop_loss_percentage: float = 3.0       # default 3% SL on BUY orders

class OrderModify(BaseModel):
    price: Optional[float] = None
    quantity: Optional[int] = None
    order_type: Optional[str] = None       # "MARKET" or "LIMIT"
    trigger_price: Optional[float] = None

class OrderResponse(BaseModel):
    id: int
    instrument: str
    quantity: int
    price: float
    order_type: str
    status: str
    created_at: datetime
    profit_loss: Optional[float] = None
    broker_order_id: Optional[str] = None
    sl_trigger_price: Optional[float] = None
    sl_broker_order_id: Optional[str] = None

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

# Kite OAuth endpoints
@app.get("/auth/kite/login")
async def kite_login():
    """Redirect user to Zerodha Kite login page."""
    login_url = KiteService.get_login_url()
    return {"login_url": login_url}

@app.get("/auth/kite/callback")
async def kite_callback(
    request_token: str,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Handle Kite OAuth callback.
    Exchanges request_token for access_token and stores it on the user record.
    """
    try:
        session = KiteService.generate_session(request_token)
        access_token = session["access_token"]

        current_user.access_token = access_token
        db.commit()

        logger.info(f"Kite access token saved for user {current_user.username}")
        return {"message": "Kite account linked successfully", "kite_user_id": session.get("user_id")}
    except Exception as e:
        logger.error(f"Kite callback error: {e}")
        raise HTTPException(status_code=400, detail="Failed to link Kite account")

# Trading endpoints
@app.get("/instruments")
async def get_instruments(
    underlying: str = "NIFTY",
    current_user: UserModel = Depends(get_current_active_user),
):
    """Get available call option instruments from Kite NFO segment."""
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked. Visit /auth/kite/login first.")
    kite = KiteService(current_user.access_token)
    instruments = kite.get_option_instruments(underlying)
    return {"instruments": instruments}

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
        
        if not current_user.access_token:
            raise HTTPException(status_code=403, detail="Kite account not linked. Visit /auth/kite/login first.")

        # Place order on Zerodha Kite
        kite = KiteService(current_user.access_token)
        broker_order_id = kite.place_order(
            tradingsymbol=order.instrument,
            transaction_type=order.order_type,
            quantity=order.quantity,
            order_type="LIMIT" if order.price else "MARKET",
            price=order.price if order.price else None,
        )

        if broker_order_id is None:
            raise HTTPException(status_code=502, detail="Kite order placement failed")

        # For BUY orders: attach a Stop-Loss Market (SL-M) SELL order
        sl_broker_order_id = None
        sl_trigger_price = None
        if order.order_type == "BUY":
            sl_pct = max(0.5, min(order.stop_loss_percentage, 50))  # clamp 0.5–50%
            sl_trigger_price = round(order.price * (1 - sl_pct / 100), 1)
            sl_broker_order_id = kite.place_sl_order(
                tradingsymbol=order.instrument,
                quantity=order.quantity,
                trigger_price=sl_trigger_price,
            )
            if sl_broker_order_id:
                logger.info(f"SL order placed: trigger=₹{sl_trigger_price} ({sl_pct}%), broker_sl_id={sl_broker_order_id}")
            else:
                logger.warning("SL order placement failed — proceeding without SL")

        # Persist to local DB
        new_order = OrderModel(
            user_id=current_user.id,
            instrument=order.instrument,
            quantity=order.quantity,
            price=order.price,
            order_type=order.order_type,
            status='PENDING',
            broker_order_id=broker_order_id,
            entry_price=order.price if order.order_type == 'BUY' else None,
            sl_percentage=order.stop_loss_percentage if order.order_type == 'BUY' else None,
            sl_trigger_price=sl_trigger_price,
            sl_broker_order_id=sl_broker_order_id,
        )

        db.add(new_order)
        db.commit()
        db.refresh(new_order)

        logger.info(f"Order placed: {order.instrument} - {order.quantity} @ ₹{order.price} by user {current_user.username}, broker_id={broker_order_id}")

        return {
            "message": "Order placed successfully",
            "order_id": new_order.id,
            "broker_order_id": broker_order_id,
            "sl_broker_order_id": sl_broker_order_id,
            "sl_trigger_price": sl_trigger_price,
            "status": "PENDING",
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Order placement error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=400, detail="Order placement failed")

@app.put("/orders/{order_id}")
async def modify_order(
    order_id: int,
    modify: OrderModify,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Modify a pending order (price, quantity, order_type).
    Only works while the order is still PENDING on Kite.
    """
    db_order = db.query(OrderModel).filter(
        OrderModel.id == order_id,
        OrderModel.user_id == current_user.id,
    ).first()
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")
    if db_order.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Cannot modify order in status '{db_order.status}'")
    if not db_order.broker_order_id:
        raise HTTPException(status_code=400, detail="Order has no broker order ID")
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked")

    kite = KiteService(current_user.access_token)
    success = kite.modify_order(
        order_id=db_order.broker_order_id,
        price=modify.price,
        quantity=modify.quantity,
        order_type=modify.order_type,
        trigger_price=modify.trigger_price,
    )
    if not success:
        raise HTTPException(status_code=502, detail="Kite order modification failed")

    # Update local record with changed fields
    if modify.price is not None:
        db_order.price = modify.price
    if modify.quantity is not None:
        db_order.quantity = modify.quantity
    db.commit()

    logger.info(f"Order {order_id} modified by user {current_user.username}")
    return {"message": "Order modified successfully", "order_id": order_id}


@app.delete("/orders/{order_id}")
async def cancel_order(
    order_id: int,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Cancel a pending order on Kite and mark it cancelled locally.
    Also cancels any attached SL order.
    """
    db_order = db.query(OrderModel).filter(
        OrderModel.id == order_id,
        OrderModel.user_id == current_user.id,
    ).first()
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")
    if db_order.status not in ("PENDING",):
        raise HTTPException(status_code=400, detail=f"Cannot cancel order in status '{db_order.status}'")
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked")

    kite = KiteService(current_user.access_token)

    # Cancel main order
    if db_order.broker_order_id:
        kite.cancel_order(db_order.broker_order_id)

    # Cancel attached SL order if present
    if db_order.sl_broker_order_id:
        kite.cancel_order(db_order.sl_broker_order_id)

    db_order.status = "CANCELLED"
    db.commit()

    logger.info(f"Order {order_id} cancelled by user {current_user.username}")
    return {"message": "Order cancelled successfully", "order_id": order_id}


@app.post("/orders/{order_id}/reverse")
async def reverse_order_on_profit(
    order_id: int,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Check if a BUY order is currently in profit and, if so, place an immediate
    SELL (MARKET) order to close the position and lock in the gain.
    Also cancels the attached SL order if present.
    """
    db_order = db.query(OrderModel).filter(
        OrderModel.id == order_id,
        OrderModel.user_id == current_user.id,
        OrderModel.order_type == "BUY",
        OrderModel.status == "EXECUTED",
    ).first()
    if not db_order:
        raise HTTPException(status_code=404, detail="No executed BUY order found with that ID")
    if db_order.exit_price is not None:
        raise HTTPException(status_code=400, detail="Position already closed")
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked")

    kite = KiteService(current_user.access_token)

    # Fetch current LTP
    ltp_map = kite.get_ltp([f"NFO:{db_order.instrument}"])
    current_price = ltp_map.get(f"NFO:{db_order.instrument}")
    if not current_price:
        raise HTTPException(status_code=502, detail="Could not fetch current price from Kite")

    # Check profit
    investment = db_order.entry_price * db_order.quantity
    current_pnl = (current_price - db_order.entry_price) * db_order.quantity
    if current_pnl <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"Position is not in profit. Current P&L: ₹{current_pnl:.2f}"
        )

    # Place SELL order to reverse/close position
    sell_broker_id = kite.place_order(
        tradingsymbol=db_order.instrument,
        transaction_type="SELL",
        quantity=db_order.quantity,
        order_type="MARKET",
    )
    if sell_broker_id is None:
        raise HTTPException(status_code=502, detail="Kite SELL order placement failed")

    # Cancel the SL order that's no longer needed
    if db_order.sl_broker_order_id:
        kite.cancel_order(db_order.sl_broker_order_id)

    # Record sell order and close buy order
    sell_record = OrderModel(
        user_id=current_user.id,
        instrument=db_order.instrument,
        quantity=db_order.quantity,
        price=current_price,
        order_type="SELL",
        status="EXECUTED",
        broker_order_id=sell_broker_id,
        strategy_id=db_order.strategy_id,
        exit_price=current_price,
        profit_loss=current_pnl,
        executed_at=datetime.now(),
    )
    db_order.exit_price = current_price
    db_order.profit_loss = current_pnl

    db.add(sell_record)
    db.commit()
    db.refresh(sell_record)

    logger.info(
        f"Reverse (profit close) on order {order_id}: SELL {db_order.quantity} "
        f"{db_order.instrument} @ ₹{current_price:.2f}, P&L=₹{current_pnl:.2f}"
    )
    return {
        "message": "Position closed at profit",
        "sell_order_id": sell_record.id,
        "sell_broker_order_id": sell_broker_id,
        "entry_price": db_order.entry_price,
        "exit_price": current_price,
        "profit_loss": round(current_pnl, 2),
    }


@app.get("/suggestions")
async def get_suggestions(
    underlying: str = "NIFTY",
    max_results: int = 5,
    min_confidence: float = 55.0,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Scan NFO call options for the given underlying and return ranked BUY suggestions.
    No orders are placed — this is a read-only analysis for human review.

    The engine checks:
      - RSI oversold + momentum reversal
      - MACD fresh crossover + histogram momentum
      - Short-term price trend confirmation
      - Queue consistency bonus from recent signal history

    Returns suggestions sorted by confidence (highest first).
    """
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked. Visit /auth/kite/login first.")

    kite = KiteService(current_user.access_token)
    engine = SignalEngine(kite=kite, db=db)

    # Fetch instruments for the underlying
    instruments = kite.get_option_instruments(underlying)
    if not instruments:
        return {"suggestions": [], "message": f"No CE instruments found for {underlying}"}

    # Limit scan to nearest 10 strikes by expiry to avoid rate limits
    instruments = sorted(instruments, key=lambda x: (x.get("expiry", ""), abs(x.get("strike", 0))))[:10]

    suggestions = []
    for inst in instruments:
        symbol = inst.get("tradingsymbol")
        token  = inst.get("instrument_token")
        if not symbol or not token:
            continue
        try:
            result = engine.evaluate(
                tradingsymbol=symbol,
                instrument_token=token,
                user_id=current_user.id,
            )
            if result["direction"] == "BUY" and result["confidence"] >= min_confidence:
                suggestions.append(result)
        except Exception as e:
            logger.warning(f"Signal evaluation failed for {symbol}: {e}")

    suggestions.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "suggestions": suggestions[:max_results],
        "scanned": len(instruments),
        "total_buy_signals": len(suggestions),
        "message": (
            f"Found {len(suggestions)} BUY signal(s) above {min_confidence}% confidence "
            f"from {len(instruments)} instruments scanned."
        ),
    }


@app.get("/signals/queue/{instrument}")
async def get_signal_queue(
    instrument: str,
    limit: int = 10,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Return the last `limit` signal evaluations for an instrument.
    Use this to inspect the signal history and spot persistent trends.
    """
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked")

    kite = KiteService(current_user.access_token)
    engine = SignalEngine(kite=kite, db=db)
    queue = engine.get_signal_queue(instrument=instrument, user_id=current_user.id, limit=limit)
    return {"instrument": instrument, "queue": queue}


@app.get("/signals/prediction/{instrument}")
async def get_signal_prediction(
    instrument: str,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Aggregate recent signal queue into a prediction summary.

    Prediction values:
      STRONG_BUY  — ≥6/10 BUY, streak ≥3, confidence rising/stable
      BUY         — ≥4/10 BUY
      HOLD        — ≥8/10 HOLD
      MIXED       — conflicting signals

    Use this to decide whether to confirm a suggestion as a manual order.
    """
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked")

    kite = KiteService(current_user.access_token)
    engine = SignalEngine(kite=kite, db=db)
    return engine.get_prediction(instrument=instrument, user_id=current_user.id)


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
    """Run trading for a specific user using their Kite access token."""
    db = next(get_db())
    try:
        user = db.query(UserModel).filter(UserModel.id == user_id).first()
        if not user or not user.access_token:
            logger.warning(f"User {user_id} has no Kite access token — trading skipped")
            return

        engine = TradingEngine(access_token=user.access_token)
        engine.run_trading_cycle()
        logger.info(f"Trading cycle completed for user {user_id}")
    except Exception as e:
        logger.error(f"Trading error for user {user_id}: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)