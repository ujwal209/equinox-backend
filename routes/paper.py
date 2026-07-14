from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
import yfinance as yf
from datetime import datetime, timedelta
from uuid import uuid4
import logging

from dependencies import get_current_user
from database.connection import (
    get_paper_portfolios_collection,
    get_paper_positions_collection,
    get_paper_orders_collection
)

router = APIRouter(prefix="/api/v1/paper", tags=["Paper Trading"])
logger = logging.getLogger("uvicorn.error")

# Constants
INITIAL_BUDGET = 1_000_000_000.0  # 1 Billion USD (Unlimited sandbox currency)

class OrderRequest(BaseModel):
    symbol: str
    quantity: int
    order_type: str # "BUY" or "SELL"
    stop_loss: Optional[float] = None

# Helper to fetch current live price and previous close, standardizing to USD
def get_live_and_prev_price(symbol: str) -> tuple[float, float]:
    yf_symbol = symbol if "." in symbol else f"{symbol}.NS"
    try:
        ticker = yf.Ticker(yf_symbol)
        history = ticker.history(period="1d", interval="1m")
        if not history.empty:
            price = float(history['Close'].iloc[-1])
        else:
            price = float(ticker.fast_info.last_price)
            
        # Try to get previous close
        try:
            prev_close = float(ticker.fast_info.previous_close)
        except:
            try:
                prev_close = float(ticker.history(period="2d")['Close'].iloc[-2])
            except:
                prev_close = price
                
        # Standardize to USD if currency is INR
        is_inr = False
        try:
            if ticker.fast_info.currency == "INR":
                is_inr = True
        except:
            if yf_symbol.endswith(".NS"):
                is_inr = True
                
        if is_inr:
            price /= 83.5
            prev_close /= 83.5
            
        return price, prev_close
    except Exception as e:
        logger.error(f"Failed to fetch live/prev prices for {yf_symbol}: {e}")
        raise HTTPException(status_code=400, detail=f"Could not fetch live price for {symbol}")

def get_live_price(symbol: str) -> float:
    price, _ = get_live_and_prev_price(symbol)
    return price

async def get_or_create_portfolio(user_id: str):
    portfolios_col = get_paper_portfolios_collection()
    portfolio = await portfolios_col.find_one({"user_id": user_id})
    if not portfolio:
        portfolio = {
            "user_id": user_id,
            "available_margin": INITIAL_BUDGET,
            "total_equity": INITIAL_BUDGET,
            "realized_pnl": 0.0,
            "created_at": datetime.utcnow()
        }
        await portfolios_col.insert_one(portfolio)
    return portfolio

@router.get("/portfolio")
async def get_portfolio(user: dict = Depends(get_current_user)):
    user_id = user["email"]
    portfolio = await get_or_create_portfolio(user_id)
    
    positions_col = get_paper_positions_collection()
    positions = await positions_col.find({"user_id": user_id}).to_list(None)
    
    # Calculate IST time from UTC (UTC+5.5)
    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    current_hour = now_ist.hour
    current_minute = now_ist.minute
    
    # Market hours are 9:15 AM to 4:00 PM IST (reset to 0 after 4 PM)
    is_market_closed = current_hour >= 16 or current_hour < 9 or (current_hour == 9 and current_minute < 15)
    
    formatted_positions = []
    total_unrealized_pnl = 0
    total_invested = 0
    total_current_value = 0
    
    for pos in positions:
        try:
            live_price, prev_close = get_live_and_prev_price(pos["symbol"])
            
            # Invested capital (absolute value represents size of position)
            qty = pos["quantity"]
            invested = abs(qty) * pos["avg_price"]
            current_value = abs(qty) * live_price
            
            # Calculate Day's P&L
            if is_market_closed:
                pnl = 0.0
                pnl_percent = 0.0
            else:
                # Compare against prev close for held shares, avg price for shares bought today
                pos_created_ist = pos["created_at"] + timedelta(hours=5, minutes=30)
                is_bought_today = pos_created_ist.date() == now_ist.date()
                base_price = pos["avg_price"] if is_bought_today else prev_close
                
                # Formula (live_price - base_price) * qty works for both long (>0) and short (<0)
                pnl = (live_price - base_price) * qty
                pnl_percent = (pnl / (base_price * abs(qty))) * 100 if base_price > 0 else 0
                
            total_unrealized_pnl += pnl
            total_invested += invested
            total_current_value += current_value
            
            formatted_positions.append({
                "symbol": pos["symbol"],
                "quantity": qty,
                "avg_price": round(pos["avg_price"], 2),
                "ltp": round(live_price, 2),
                "invested": round(invested, 2),
                "current_value": round(current_value, 2),
                "pnl": round(pnl, 2),
                "pnl_percent": round(pnl_percent, 2)
            })
        except Exception as e:
            logger.error(f"Error processing position {pos['symbol']}: {e}")
            
    # Calculate today's realized P&L from orders executed today
    if is_market_closed:
        today_realized_pnl = 0.0
    else:
        today_ist_start_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=5, minutes=30)
        orders_col = get_paper_orders_collection()
        today_orders = await orders_col.find({
            "user_id": user_id,
            "timestamp": {"$gte": today_ist_start_utc}
        }).to_list(None)
        today_realized_pnl = sum(o.get("realized_pnl", 0.0) for o in today_orders)
        
    portfolio["realized_pnl"] = today_realized_pnl
    portfolio["total_equity"] = portfolio["available_margin"] + total_current_value
    portfolio["total_unrealized_pnl"] = total_unrealized_pnl
    portfolio["total_pnl"] = total_unrealized_pnl + today_realized_pnl
    portfolio["total_invested"] = total_invested
    
    pnl_percent = 0.0
    if total_invested > 0:
        pnl_percent = (portfolio["total_pnl"] / total_invested) * 100
    portfolio["total_pnl_percent"] = round(pnl_percent, 2)
    
    # Remove MongoDB _id from response
    portfolio.pop("_id", None)
    
    return {
        "portfolio": portfolio,
        "positions": formatted_positions
    }

@router.post("/order")
async def place_order(order: OrderRequest, user: dict = Depends(get_current_user)):
    user_id = user["email"]
    
    if order.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")
        
    order_type = order.order_type.upper()
    if order_type not in ["BUY", "SELL"]:
        raise HTTPException(status_code=400, detail="Order type must be BUY or SELL")
        
    portfolio = await get_or_create_portfolio(user_id)
    live_price = get_live_price(order.symbol)
    total_trade_value = live_price * order.quantity
    
    positions_col = get_paper_positions_collection()
    portfolios_col = get_paper_portfolios_collection()
    orders_col = get_paper_orders_collection()
    
    current_position = await positions_col.find_one({"user_id": user_id, "symbol": order.symbol})
    
    trade_pnl = 0.0
    
    if order_type == "BUY":
        # Cover/Buyback logic if they had a SHORT position (quantity < 0)
        if current_position and current_position["quantity"] < 0:
            covered_qty = min(order.quantity, abs(current_position["quantity"]))
            # Short cover profit is (short_avg - live_price) * covered_qty
            trade_pnl = (current_position["avg_price"] - live_price) * covered_qty
            
            new_qty = current_position["quantity"] + order.quantity
            
            # Adjust margin and realized P&L
            await portfolios_col.update_one(
                {"user_id": user_id},
                {"$inc": {"available_margin": (covered_qty * live_price), "realized_pnl": trade_pnl}}
            )
            
            if new_qty == 0:
                await positions_col.delete_one({"_id": current_position["_id"]})
            elif new_qty < 0:
                # Still short, but smaller position
                await positions_col.update_one(
                    {"_id": current_position["_id"]},
                    {"$set": {"quantity": new_qty, "updated_at": datetime.utcnow()}}
                )
            else:
                # Reversed to LONG position
                remaining_buy = order.quantity - covered_qty
                await portfolios_col.update_one(
                    {"user_id": user_id},
                    {"$inc": {"available_margin": -(remaining_buy * live_price)}}
                )
                await positions_col.update_one(
                    {"_id": current_position["_id"]},
                    {"$set": {"quantity": new_qty, "avg_price": live_price, "updated_at": datetime.utcnow()}}
                )
        else:
            # Normal LONG buy
            cost = total_trade_value
            await portfolios_col.update_one(
                {"user_id": user_id},
                {"$inc": {"available_margin": -cost}}
            )
            
            if current_position:
                new_qty = current_position["quantity"] + order.quantity
                new_avg_price = ((current_position["quantity"] * current_position["avg_price"]) + total_trade_value) / new_qty
                await positions_col.update_one(
                    {"_id": current_position["_id"]},
                    {"$set": {"quantity": new_qty, "avg_price": new_avg_price, "updated_at": datetime.utcnow()}}
                )
            else:
                await positions_col.insert_one({
                    "user_id": user_id,
                    "symbol": order.symbol,
                    "quantity": order.quantity,
                    "avg_price": live_price,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                })
                
    elif order_type == "SELL":
        # Exit/Sellback logic if they had a LONG position (quantity > 0)
        if current_position and current_position["quantity"] > 0:
            sold_qty = min(order.quantity, current_position["quantity"])
            # Long exit profit is (live_price - long_avg) * sold_qty
            trade_pnl = (live_price - current_position["avg_price"]) * sold_qty
            
            new_qty = current_position["quantity"] - order.quantity
            
            # Adjust margin and realized P&L
            await portfolios_col.update_one(
                {"user_id": user_id},
                {"$inc": {"available_margin": (sold_qty * live_price), "realized_pnl": trade_pnl}}
            )
            
            if new_qty == 0:
                await positions_col.delete_one({"_id": current_position["_id"]})
            elif new_qty > 0:
                # Still long, but smaller position
                await positions_col.update_one(
                    {"_id": current_position["_id"]},
                    {"$set": {"quantity": new_qty, "updated_at": datetime.utcnow()}}
                )
            else:
                # Reversed to SHORT position (short selling)
                remaining_short = order.quantity - sold_qty
                await portfolios_col.update_one(
                    {"user_id": user_id},
                    {"$inc": {"available_margin": (remaining_short * live_price)}}
                )
                await positions_col.update_one(
                    {"_id": current_position["_id"]},
                    {"$set": {"quantity": new_qty, "avg_price": live_price, "updated_at": datetime.utcnow()}}
                )
        else:
            # Normal SHORT opening / addition
            revenue = total_trade_value
            await portfolios_col.update_one(
                {"user_id": user_id},
                {"$inc": {"available_margin": revenue}}
            )
            
            if current_position:
                # Already short (qty is negative)
                old_qty = current_position["quantity"]
                new_qty = old_qty - order.quantity
                new_avg_price = ((abs(old_qty) * current_position["avg_price"]) + total_trade_value) / abs(new_qty)
                await positions_col.update_one(
                    {"_id": current_position["_id"]},
                    {"$set": {"quantity": new_qty, "avg_price": new_avg_price, "updated_at": datetime.utcnow()}}
                )
            else:
                # New short position
                await positions_col.insert_one({
                    "user_id": user_id,
                    "symbol": order.symbol,
                    "quantity": -order.quantity,
                    "avg_price": live_price,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                })
                
    # Record the order in ledger
    order_doc = {
        "order_id": str(uuid4()),
        "user_id": user_id,
        "symbol": order.symbol,
        "order_type": order_type,
        "quantity": order.quantity,
        "price": live_price,
        "stop_loss": order.stop_loss,
        "total_value": total_trade_value,
        "realized_pnl": trade_pnl,
        "status": "EXECUTED",
        "timestamp": datetime.utcnow()
    }
    await orders_col.insert_one(order_doc)
    order_doc.pop("_id", None)
    
    return {
        "message": f"Successfully executed {order_type} for {order.quantity} shares of {order.symbol} at {live_price}",
        "order": order_doc
    }

@router.get("/statements")
async def get_statements(user: dict = Depends(get_current_user)):
    user_id = user["email"]
    orders_col = get_paper_orders_collection()
    orders = await orders_col.find({"user_id": user_id}).sort("timestamp", -1).to_list(None)
    for o in orders:
        o.pop("_id", None)
    return {"statements": orders}
