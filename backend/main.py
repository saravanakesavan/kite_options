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
from database import get_db, create_tables, SessionLocal
from auth import AuthService, get_current_active_user
from models import (
    User as UserModel,
    Order as OrderModel,
    TradingStrategy as StrategyModel,
    PositionAlert as AlertModel,
)
from dotenv import load_dotenv

# Import trading engine with error handling
try:
    from trading_engine import TradingEngine
except ImportError as e:
    logger.warning(f"Trading engine import failed: {e}. Some features may be limited.")

from kite_service import KiteService
from signal_engine import SignalEngine
from models import SignalRecord as SignalRecordModel
import position_monitor as pm

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

    # Resume monitoring for all users who had active sessions before restart
    db = SessionLocal()
    try:
        resumed = pm.resume_all_from_db(db)
        logger.info(f"Resumed {resumed} monitoring session(s) from DB")
    finally:
        db.close()

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

@app.post("/auth/login")
async def login(user: UserLogin, db: Session = Depends(get_db)):
    """
    Authenticate user and return access token.
    Automatically resumes position monitoring if user had an active session.
    """
    try:
        authenticated_user = AuthService.authenticate_user(
            db=db, username=user.username, password=user.password
        )
        if not authenticated_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )

        access_token = AuthService.create_access_token(
            data={"sub": authenticated_user.username}
        )

        # Auto-resume monitoring if this user had an active session
        monitor_resumed = False
        if authenticated_user.access_token:
            monitor_resumed = pm.resume_if_active(
                authenticated_user.id, authenticated_user.access_token, db
            )
            if monitor_resumed:
                logger.info(f"Monitoring auto-resumed for {authenticated_user.username} on login")

        logger.info(f"User logged in: {user.username}")
        return {
            "access_token"    : access_token,
            "token_type"      : "bearer",
            "monitor_resumed" : monitor_resumed,
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
    symbol        = f"NFO:{db_order.instrument}"
    current_price = kite.get_ltp([symbol]).get(symbol)
    if not current_price:
        raise HTTPException(status_code=502, detail="Could not fetch current price from Kite")

    # Check profit
    current_pnl = (current_price - db_order.entry_price) * db_order.quantity
    if current_pnl <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"Position is not in profit. Current P&L: ₹{current_pnl:.2f}"
        )

    sell_broker_id, current_pnl, sell_record = pm.execute_sell_and_record(
        kite, db, db_order, current_user.id, current_price
    )
    if not sell_broker_id:
        raise HTTPException(status_code=502, detail="Kite SELL order placement failed")

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


@app.get("/alerts")
async def get_alerts(
    unactioned_only: bool = True,
    limit: int = 50,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Return position alerts written by the monitor.
    Alerts are ordered newest-first.
    Set unactioned_only=false to see full history including already-exited alerts.
    """
    query = db.query(AlertModel).filter(AlertModel.user_id == current_user.id)
    if unactioned_only:
        query = query.filter(AlertModel.is_actioned.is_(False))
    alerts = query.order_by(AlertModel.created_at.desc()).limit(limit).all()

    return {
        "alerts": [
            {
                "id"           : a.id,
                "order_id"     : a.order_id,
                "instrument"   : a.instrument,
                "alert_type"   : a.alert_type,
                "message"      : a.message,
                "current_price": a.current_price,
                "entry_price"  : a.entry_price,
                "pnl"          : a.pnl,
                "pnl_pct"      : a.pnl_pct,
                "is_actioned"  : a.is_actioned,
                "created_at"   : a.created_at,
            }
            for a in alerts
        ],
        "count": len(alerts),
    }


@app.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(
    alert_id: int,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Dismiss a WATCHING alert without taking action."""
    alert = db.query(AlertModel).filter(
        AlertModel.id == alert_id,
        AlertModel.user_id == current_user.id,
    ).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_actioned = True
    db.commit()
    return {"message": "Alert dismissed", "alert_id": alert_id}


@app.post("/positions/{order_id}/exit")
async def single_click_exit(
    order_id: int,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Single-click exit for an open position.
    Fetches live LTP, places SELL MARKET on Kite, cancels SL order,
    records P&L, and marks all pending alerts for this order as actioned.
    Works regardless of whether the position is in profit or loss.
    """
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked")

    order = db.query(OrderModel).filter(
        OrderModel.id        == order_id,
        OrderModel.user_id   == current_user.id,
        OrderModel.order_type == "BUY",
        OrderModel.status    == "EXECUTED",
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Open BUY position not found")
    if order.exit_price is not None:
        raise HTTPException(status_code=400, detail="Position already closed")

    kite = KiteService(current_user.access_token)

    # Live price
    symbol        = f"NFO:{order.instrument}"
    current_price = kite.get_ltp([symbol]).get(symbol)
    if not current_price:
        raise HTTPException(status_code=502, detail="Could not fetch LTP from Kite")

    sell_broker_id, pnl, sell_record = pm.execute_sell_and_record(
        kite, db, order, current_user.id, current_price
    )
    if not sell_broker_id:
        raise HTTPException(status_code=502, detail="SELL order failed on Kite")

    # Mark all pending alerts for this order as actioned
    db.query(AlertModel).filter(
        AlertModel.order_id    == order_id,
        AlertModel.is_actioned.is_(False),
    ).update({"is_actioned": True})

    db.commit()
    db.refresh(sell_record)

    logger.info(
        f"Single-click exit: {order.instrument} @ ₹{current_price:.2f} "
        f"P&L=₹{pnl:+.2f} by {current_user.username}"
    )
    return {
        "message"             : "Position exited",
        "instrument"          : order.instrument,
        "sell_order_id"       : sell_record.id,
        "sell_broker_order_id": sell_broker_id,
        "entry_price"         : order.entry_price,
        "exit_price"          : current_price,
        "quantity"            : order.quantity,
        "profit_loss"         : round(pnl, 2),
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

@app.post("/monitoring/start")
async def start_position_monitoring(
    current_user: UserModel = Depends(get_current_active_user),
):
    """
    Start the 1-minute position monitor for this user.
    The monitor checks every open BUY position and auto-exits on:
      - Profit target hit (₹300–₹500)
      - Extended profit above ₹500 (exit before reversal)
      - Stop-loss breach (per-order sl_percentage, default 3%)
      - Force exit at 1:25 PM IST (before Kite auto-squareoff)

    Auto-entry remains manual — only exits are automated.
    """
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked. Visit /auth/kite/login first.")
    if pm.is_running(current_user.id):
        return {"message": "Monitor already running", "user_id": current_user.id}

    started = pm.start_monitor(user_id=current_user.id, access_token=current_user.access_token, db=db)
    if not started:
        raise HTTPException(status_code=409, detail="Could not start monitor")

    logger.info(f"Position monitor started for user {current_user.username}")
    return {
        "message": "Position monitor started — checking every 60 seconds",
        "user_id": current_user.id,
        "exit_rules": {
            "profit_target": "₹300–₹500",
            "extended_profit": "> ₹500 exits immediately",
            "stop_loss": "per-order sl_percentage (default 3%)",
            "force_exit": "1:25 PM IST",
        },
    }


@app.post("/monitoring/stop")
async def stop_position_monitoring(
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Stop the position monitor for this user."""
    stopped = pm.stop_monitor(current_user.id, db=db)
    if not stopped:
        return {"message": "Monitor was not running", "user_id": current_user.id}
    logger.info(f"Position monitor stop requested for user {current_user.username}")
    return {"message": "Monitor stop requested — will halt within 1 second", "user_id": current_user.id}


@app.get("/monitoring/status")
async def get_monitoring_status(
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Return monitoring status and a live snapshot of all open positions
    with their current P&L from Kite.
    """
    if not current_user.access_token:
        raise HTTPException(status_code=403, detail="Kite account not linked")

    open_orders = (
        db.query(OrderModel)
        .filter(
            OrderModel.user_id    == current_user.id,
            OrderModel.order_type == "BUY",
            OrderModel.status     == "EXECUTED",
            OrderModel.exit_price.is_(None),
        )
        .all()
    )

    positions = []
    if open_orders:
        kite    = KiteService(current_user.access_token)
        symbols = list({f"NFO:{o.instrument}" for o in open_orders})
        ltp_map = kite.get_ltp(symbols)

        for o in open_orders:
            ltp = ltp_map.get(f"NFO:{o.instrument}")
            pnl = round((ltp - o.entry_price) * o.quantity, 2) if ltp and o.entry_price else None
            pnl_pct = round((ltp - o.entry_price) / o.entry_price * 100, 2) if ltp and o.entry_price else None
            positions.append({
                "order_id"    : o.id,
                "instrument"  : o.instrument,
                "quantity"    : o.quantity,
                "entry_price" : o.entry_price,
                "current_ltp" : ltp,
                "pnl"         : pnl,
                "pnl_pct"     : pnl_pct,
                "sl_trigger"  : o.sl_trigger_price,
                "sl_pct"      : o.sl_percentage,
            })

    return {
        "monitor_running" : pm.is_running(current_user.id),
        "open_positions"  : len(positions),
        "positions"       : positions,
        "poll_interval_s" : pm.POLL_INTERVAL_SEC,
        "force_exit_time" : f"{pm.FORCE_EXIT_HOUR:02d}:{pm.FORCE_EXIT_MINUTE:02d} IST",
    }


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