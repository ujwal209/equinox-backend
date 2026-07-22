import logging
import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
import httpx

from dependencies import get_current_user
from database.connection import (
    get_user_collection,
    get_watchlist_collection,
    get_companies_collection
)
from services.email import send_intraday_watchlist_email
from config.keys import tavily_keys, groq_keys
from groq import AsyncGroq

router = APIRouter(prefix="/api/v1/user/email-alerts", tags=["Email Alerts"])
logger = logging.getLogger("uvicorn.error")

class EmailAlertSettings(BaseModel):
    frequency: str = "1h" # "1h", "2h", "4h", "daily", "off"
    enabled: bool = True
    intraday_focus: bool = True

def get_tavily_key() -> str:
    try:
        return tavily_keys.get_next_key()
    except Exception:
        return ""

def get_groq_key() -> str:
    try:
        return groq_keys.get_next_key()
    except Exception:
        return ""

async def query_groq_json(system_prompt: str, user_prompt: str) -> dict:
    key = get_groq_key()
    if not key:
        return {}
    models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    for model in models:
        try:
            client = AsyncGroq(api_key=key)
            completion = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_completion_tokens=1024,
                response_format={"type": "json_object"}
            )
            content = completion.choices[0].message.content
            if content:
                import json
                return json.loads(content)
        except Exception as e:
            logger.warning(f"Groq API error for model {model}: {e}")
    return {}

def get_company_logo_url(symbol: str, name: str) -> str:
    clean = symbol.split(".")[0].upper()
    exchange = "BSE" if symbol.upper().endswith(".BO") else ("NSE" if "." in symbol else "US")
    return f"https://eodhd.com/img/logos/{exchange}/{clean}.png"

async def build_watchlist_ai_digest(user_email: str) -> List[dict]:
    watchlist_col = get_watchlist_collection()
    companies_col = get_companies_collection()
    
    watchlists = await watchlist_col.find({"user_email": user_email}).to_list(length=20)
    symbols = set()
    for w in watchlists:
        for sym in w.get("symbols", []):
            symbols.add(sym.upper())
            
    if not symbols:
        return []
        
async def fetch_stock_news(client: httpx.AsyncClient, symbol: str, name: str, tavily_key: Optional[str]):
    clean_sym = symbol.split('.')[0].upper()
    clean_name = name.replace('.NS', '').replace('.BO', '').replace('Limited', '').replace('Ltd', '').strip()
    if clean_name == clean_sym or len(clean_name) <= 2:
        clean_name = f"{clean_sym} Stock"
        
    search_query = f"{clean_name} ({clean_sym}) stock news share price India" if (symbol.endswith('.NS') or symbol.endswith('.BO')) else f"{clean_name} ({clean_sym}) stock news"

    sources = []
    scraped_texts = []

    # 1. Try Tavily search first
    if tavily_key:
        try:
            res = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": search_query,
                    "search_depth": "advanced",
                    "max_results": 4
                }
            )
            if res.status_code == 200:
                results = res.json().get("results", [])
                for r in results:
                    url = r.get("url", "")
                    title = r.get("title", "")
                    content = r.get("content", "")
                    if url and title:
                        sources.append({"title": title, "url": url})
                        scraped_texts.append(f"Article: {title}\nSnippet: {content}")
        except Exception as e:
            logger.warning(f"Tavily search error for {symbol}: {e}")

    # 2. Fallback to Google News RSS if no sources found
    if not sources:
        try:
            from urllib.parse import quote
            import xml.etree.ElementTree as ET
            rss_url = f"https://news.google.com/rss/search?q={quote(search_query)}&hl=en-IN&gl=IN&ceid=IN:en"
            rss_res = await client.get(rss_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            if rss_res.status_code == 200:
                root = ET.fromstring(rss_res.text)
                items = root.findall(".//item")
                for item in items[:4]:
                    title_elem = item.find("title")
                    link_elem = item.find("link")
                    title = title_elem.text if title_elem is not None else ""
                    url = link_elem.text if link_elem is not None else ""
                    if title and url:
                        sources.append({"title": title, "url": url})
                        scraped_texts.append(f"Article: {title}")
        except Exception as e:
            logger.warning(f"RSS fallback news error for {symbol}: {e}")

    # 3. Deep body extraction with trafilatura for top sources
    for i, src in enumerate(sources[:2]):
        url = src.get("url")
        if url:
            try:
                import trafilatura
                downloaded = trafilatura.fetch_url(url)
                if downloaded:
                    extracted = trafilatura.extract(downloaded, include_links=False, include_images=False)
                    if extracted and len(extracted) > 100:
                        scraped_texts[i] += f"\nDeep Content: {extracted[:1200]}"
            except Exception:
                pass

    return sources, scraped_texts

async def build_watchlist_ai_digest(user_email: str) -> List[dict]:
    watchlist_col = get_watchlist_collection()
    companies_col = get_companies_collection()
    
    watchlists = await watchlist_col.find({"user_email": user_email}).to_list(length=20)
    symbols = set()
    for w in watchlists:
        for sym in w.get("symbols", []):
            symbols.add(sym.upper())
            
    if not symbols:
        return []
        
    symbols = sorted(list(symbols))[:8] # Analyze top 8 watchlist symbols
    
    companies = await companies_col.find({"symbol": {"$in": symbols}}).to_list(length=50)
    company_map = {c["symbol"]: c["name"] for c in companies}
    
    digest_items = []
    tavily_key = get_tavily_key()
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        for sym in symbols:
            name = company_map.get(sym, sym.split('.')[0])
            price = 0.0
            change_pct = 0.0
            currency_symbol = "₹"
            
            # Fetch live price from Yahoo
            try:
                from urllib.parse import quote
                res = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                if res.status_code == 200:
                    meta = res.json()["chart"]["result"][0]["meta"]
                    price = meta.get("regularMarketPrice", 0.0) or 0.0
                    prev_close = meta.get("chartPreviousClose", 0.0) or price
                    
                    raw_curr = meta.get("currency", "INR")
                    if raw_curr != "INR" and not (sym.endswith(".NS") or sym.endswith(".BO")):
                        currency_symbol = "$"
                    else:
                        currency_symbol = "₹"
                        
                    change = price - prev_close
                    change_pct = (change / prev_close) * 100 if prev_close else 0.0
            except Exception:
                pass
                
            # Deep search stock news
            sources, scraped_texts = await fetch_stock_news(client, sym, name, tavily_key)
            combined_news = "\n---\n".join(scraped_texts) if scraped_texts else "No recent breaking news found."
            
            system_prompt = (
                "You are an expert intraday stock market analyst.\n"
                "Analyze the provided stock news and market data. Output ONLY a valid JSON object matching this schema:\n"
                "{\n"
                "  \"sentiment\": \"BULLISH\" | \"BEARISH\" | \"NEUTRAL\",\n"
                "  \"summary\": \"2-3 sentence intraday catalyst summary based strictly on the scraped news.\",\n"
                "  \"suggestion\": \"Actionable intraday strategy (e.g. Buy on dip near key support / Watch resistance)\"\n"
                "}"
            )
            user_prompt = f"Stock: {sym} ({name})\nCurrent Price: {currency_symbol}{price:.2f} ({change_pct:.2f}%)\nDeep Scraped Stock News:\n{combined_news}"
            
            ai_res = await query_groq_json(system_prompt, user_prompt)
            
            logo_url = get_company_logo_url(sym, name)
            
            digest_items.append({
                "symbol": sym,
                "name": name,
                "price": round(price, 2),
                "changePercent": round(change_pct, 2),
                "currency_symbol": currency_symbol,
                "logo_url": logo_url,
                "sentiment": ai_res.get("sentiment", "NEUTRAL"),
                "summary": ai_res.get("summary", "Market consolidating around current technical levels."),
                "suggestion": ai_res.get("suggestion", "Watch price action around key support/resistance."),
                "sources": sources
            })
            
    return digest_items

@router.get("")
async def get_email_alert_settings(user: dict = Depends(get_current_user)):
    user_col = get_user_collection()
    user_doc = await user_col.find_one({"email": user["email"]})
    
    settings_data = user_doc.get("email_alerts", {
        "frequency": "1h",
        "enabled": True,
        "intraday_focus": True,
        "last_sent_at": None
    }) if user_doc else {}
    
    return {
        "email": user["email"],
        "settings": settings_data
    }

@router.post("")
async def update_email_alert_settings(payload: EmailAlertSettings, user: dict = Depends(get_current_user)):
    user_col = get_user_collection()
    
    update_data = {
        "email_alerts.frequency": payload.frequency,
        "email_alerts.enabled": payload.enabled,
        "email_alerts.intraday_focus": payload.intraday_focus,
        "email_alerts.updated_at": datetime.utcnow().isoformat()
    }
    
    await user_col.update_one({"email": user["email"]}, {"$set": update_data}, upsert=True)
    return {"message": "Email alert settings updated successfully", "settings": payload.dict()}

@router.post("/send-test")
async def send_test_digest(user: dict = Depends(get_current_user)):
    user_email = user["email"]
    logger.info(f"Generating manual test AI Watchlist Digest for {user_email}")
    
    items = await build_watchlist_ai_digest(user_email)
    if not items:
        raise HTTPException(status_code=400, detail="Your watchlist is empty. Add stocks to your watchlist first to receive AI email digests.")
        
    success = send_intraday_watchlist_email(user_email, items, frequency_label="Manual Test")
    if success:
        return {"message": f"Test intraday AI watchlist digest sent successfully to {user_email}", "analyzed_count": len(items)}
    else:
        raise HTTPException(status_code=500, detail="Failed to send test email. Check server SMTP configuration.")

@router.api_route("/trigger", methods=["GET", "POST"])
async def trigger_automated_email_digests(request: Request):
    """
    Serverless trigger endpoint designed to run during active market hours (9:15 AM - 3:30 PM IST).
    """
    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    weekday = now_ist.weekday() # 0 = Mon, ..., 4 = Fri
    current_minutes = now_ist.hour * 60 + now_ist.minute
    open_minutes = 9 * 60 + 15   # 9:15 AM IST
    close_minutes = 15 * 60 + 30 # 3:30 PM IST
    
    is_market_open = (weekday < 5) and (open_minutes <= current_minutes < close_minutes)
    if not is_market_open:
        return {
            "status": "market_closed",
            "message": "Market is currently closed. Intraday email digests execute during market hours (9:15 AM - 3:30 PM IST, Mon-Fri).",
            "processed_digests": 0
        }
        
    user_col = get_user_collection()
    users = await user_col.find({"$or": [{"email_alerts.enabled": True}, {"email_alerts": {"$exists": False}}]}).to_list(length=100)
    
    processed_count = 0
    now_utc = datetime.now(timezone.utc)
    
    interval_hours_map = {
        "1h": 1,
        "2h": 2,
        "4h": 4,
        "daily": 24
    }
    
    for u in users:
        email = u.get("email")
        alerts_cfg = u.get("email_alerts", {})
        enabled = alerts_cfg.get("enabled", True)
        freq = alerts_cfg.get("frequency", "1h") # Default to 1h hourly if unconfigured
        
        if not enabled or freq == "off" or not email:
            continue
            
        required_hours = interval_hours_map.get(freq, 1)
        last_sent_str = alerts_cfg.get("last_sent_at")
        
        should_send = False
        if not last_sent_str:
            should_send = True
        else:
            try:
                last_sent = datetime.fromisoformat(last_sent_str)
                if last_sent.tzinfo is None:
                    last_sent = last_sent.replace(tzinfo=timezone.utc)
                if (now_utc - last_sent) >= timedelta(hours=required_hours):
                    should_send = True
            except Exception:
                should_send = True
                
        if should_send:
            logger.info(f"Executing scheduled intraday AI digest for {email} ({freq} schedule)")
            items = await build_watchlist_ai_digest(email)
            if items:
                freq_label = "Hourly" if freq == "1h" else f"Every {freq}" if freq != "daily" else "Daily"
                send_intraday_watchlist_email(email, items, frequency_label=freq_label)
                await user_col.update_one(
                    {"_id": u["_id"]},
                    {"$set": {"email_alerts.last_sent_at": now_utc.isoformat()}}
                )
                processed_count += 1
                
    return {
        "status": "success",
        "processed_digests": processed_count,
        "timestamp": now_utc.isoformat()
    }

async def start_email_alerts_background_worker():
    """
    Background worker loop that runs continuously on the backend server.
    Triggers scheduled watchlist email digests automatically in the background even if the user is not actively using the app.
    """
    logger.info("Starting background email alerts worker loop...")
    while True:
        try:
            await asyncio.sleep(300) # Check every 5 minutes
            now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
            weekday = now_ist.weekday()
            current_minutes = now_ist.hour * 60 + now_ist.minute
            open_minutes = 9 * 60 + 15
            close_minutes = 15 * 60 + 30
            
            if (weekday < 5) and (open_minutes <= current_minutes < close_minutes):
                user_col = get_user_collection()
                users = await user_col.find({"$or": [{"email_alerts.enabled": True}, {"email_alerts": {"$exists": False}}]}).to_list(length=100)
                now_utc = datetime.now(timezone.utc)
                interval_hours_map = {"1h": 1, "2h": 2, "4h": 4, "daily": 24}
                
                for u in users:
                    email = u.get("email")
                    alerts_cfg = u.get("email_alerts", {})
                    enabled = alerts_cfg.get("enabled", True)
                    freq = alerts_cfg.get("frequency", "1h")
                    
                    if not enabled or freq == "off" or not email:
                        continue
                        
                    required_hours = interval_hours_map.get(freq, 1)
                    last_sent_str = alerts_cfg.get("last_sent_at")
                    
                    should_send = False
                    if not last_sent_str:
                        should_send = True
                    else:
                        try:
                            last_sent = datetime.fromisoformat(last_sent_str)
                            if last_sent.tzinfo is None:
                                last_sent = last_sent.replace(tzinfo=timezone.utc)
                            if (now_utc - last_sent) >= timedelta(hours=required_hours):
                                should_send = True
                        except Exception:
                            should_send = True
                            
                    if should_send:
                        logger.info(f"[Background Worker] Executing scheduled intraday AI digest for {email} ({freq} schedule)")
                        items = await build_watchlist_ai_digest(email)
                        if items:
                            freq_label = "Hourly" if freq == "1h" else f"Every {freq}" if freq != "daily" else "Daily"
                            send_intraday_watchlist_email(email, items, frequency_label=freq_label)
                            await user_col.update_one(
                                {"_id": u["_id"]},
                                {"$set": {"email_alerts.last_sent_at": now_utc.isoformat()}}
                            )
        except asyncio.CancelledError:
            logger.info("Background email alerts worker stopped.")
            break
        except Exception as e:
            logger.error(f"Error in background email worker loop: {e}")

