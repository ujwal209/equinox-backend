import os
import logging
import json
import asyncio
from datetime import datetime
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, status, Depends, Request
from dependencies import get_current_user
from pydantic import BaseModel
import httpx
from groq import AsyncGroq

router = APIRouter(prefix="/api/v1/market", tags=["Market Data"])
logger = logging.getLogger("uvicorn.error")

# Load Tavily API keys
tavily_keys = [k.strip() for k in os.getenv("TAVILY_API_KEYS", "").split(",") if k.strip()]
tavily_index = 0

def get_tavily_key():
    global tavily_index
    if not tavily_keys:
        return None
    key = tavily_keys[tavily_index]
    tavily_index = (tavily_index + 1) % len(tavily_keys)
    return key


# Load environment keys
groq_keys = [k.strip() for k in os.getenv("GROQ_API_KEYS", "").split(",") if k.strip()]

groq_index = 0

def get_groq_key():
    global groq_index
    if not groq_keys:
        return None
    key = groq_keys[groq_index]
    groq_index = (groq_index + 1) % len(groq_keys)
    return key

async def query_groq(system_prompt: str, user_prompt: str, requested_model: str = "llama-3.3-70b-versatile") -> str:
    if not groq_keys:
        return ""
    
    models_to_try = [
        requested_model,
        "llama-3.1-8b-instant",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b"
    ]
    seen = set()
    models_to_try = [x for x in models_to_try if not (x in seen or seen.add(x))]
    
    for _ in range(min(3, len(groq_keys))):
        key = get_groq_key()
        if not key:
            continue
            
        for model in models_to_try:
            try:
                client = AsyncGroq(api_key=key)
                completion = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.0,
                    max_completion_tokens=1024,
                    response_format={"type": "json_object"}
                )
                content = completion.choices[0].message.content
                if content:
                    return content
            except Exception as e:
                logger.warning(f"Groq request to model {model} failed: {e}. Trying fallback.")
                continue
                
        logger.error(f"All model fallbacks failed for key.")
    return ""

class SearchQuery(BaseModel):
    query: str

class TimeseriesRequest(BaseModel):
    symbol: str
    range: str = "1mo"
    interval: str = "1d"

@router.get("/live")
async def get_live_market_data():
    try:
        from database.connection import get_indices_collection, get_companies_collection
        indices_col = get_indices_collection()
        companies_col = get_companies_collection()
        
        # Pull core indices
        db_indices = await indices_col.find({}).to_list(length=10)
        # Dynamically sample 15 random companies from our 1000+ DB for the live feed
        db_companies = await companies_col.aggregate([{"$sample": {"size": 15}}]).to_list(length=15)
        
        all_assets = db_indices + db_companies
        
        stock_symbols = [a["symbol"] for a in all_assets if a.get("exchDisp") != "CRYPTO"]
        crypto_symbols = [a["symbol"] for a in all_assets if a.get("exchDisp") == "CRYPTO"]
        
        results = []
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # We process Yahoo history to build the sparklines, and optionally override price via Tavily
            for sym in stock_symbols:
                try:
                    safe_sym = quote(sym)
                    # Get baseline history from Yahoo
                    res = await client.get(
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{safe_sym}",
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                            "Accept": "application/json"
                        }
                    )
                    
                    if res.status_code == 200:
                        try:
                            data = res.json()
                        except Exception as e:
                            logger.error(f"Failed to parse Yahoo JSON for {sym}: {e}")
                            data = {}

                        if data.get("chart") and data["chart"].get("result"):
                            meta = data["chart"]["result"][0]["meta"]
                            yahoo_price = meta.get("regularMarketPrice")
                            prev_close = meta.get("chartPreviousClose")
                            
                            history = []
                            try:
                                quotes = data["chart"]["result"][0]["indicators"]["quote"][0]
                                closes = quotes.get("close", [])
                                history = [float(c) for c in closes if c is not None][-10:]
                            except Exception:
                                pass
                                
                            db_info = next((a for a in all_assets if a["symbol"] == sym), {})
                            name = db_info.get("name") or meta.get("shortName") or sym
                            logo = db_info.get("logo", "")
                            
                            # Parallel strategy: use Yahoo baseline, optionally trigger Tavily for top assets 
                            # (to save Tavily quota, we might only use it heavily in timeseries, but for live feed we can rely mostly on Yahoo to keep load times < 2s)
                            # Let's use Yahoo price by default to ensure Homepage loads instantly, 
                            # but we implement Tavily lookup strictly for single-stock timeseries view where detailed latency is acceptable.
                            
                            currency = meta.get("currency")
                            if currency == "INR":
                                if yahoo_price is not None: yahoo_price /= 83.5
                                if prev_close is not None: prev_close /= 83.5
                                history = [h / 83.5 for h in history]
                            
                            if yahoo_price is not None and prev_close is not None:
                                change = yahoo_price - prev_close
                                change_percent = (change / prev_close) * 100
                                
                                results.append({
                                    "symbol": sym,
                                    "name": name,
                                    "logo": logo,
                                    "price": round(yahoo_price, 2),
                                    "change": round(change, 2),
                                    "changePercent": round(change_percent, 2),
                                    "history": history
                                })
                except Exception as e:
                    logger.error(f"Error fetching stock {sym}: {e}")
            
            # Fetch crypto rates from Binance
            crypto_prices = []
            try:
                res = await client.get("https://api.binance.com/api/v3/ticker/price")
                if res.status_code == 200:
                    crypto_prices = res.json()
            except Exception as e:
                logger.error(f"Error fetching crypto prices from Binance: {e}")
                
            def get_crypto(sym):
                item = next((c for c in crypto_prices if c["symbol"] == f"{sym}USDT"), None)
                if not item:
                    return None
                price = float(item["price"])
                db_info = next((a for a in all_assets if a["symbol"] == sym), {})
                
                return {
                    "symbol": sym,
                    "name": db_info.get("name") or f"{sym} (Live Crypto)",
                    "logo": db_info.get("logo", ""),
                    "price": round(price, 2),
                    "change": round(price * 0.008, 2), # Mock crypto daily change since binance ticker/price doesn't give 24h change easily in this endpoint
                    "changePercent": 0.8,
                    "history": [price * 0.985, price * 0.99, price * 0.995, price]
                }
                
            for c_sym in crypto_symbols:
                crypto_data = get_crypto(c_sym)
                if crypto_data:
                    results.append(crypto_data)
                    
        return results
    except Exception as e:
        logger.error(f"Live market data query crashed: {e}")
        return []

@router.post("/search")
async def search_tickers(body: SearchQuery):
    query = body.query.strip()
    if not query:
        return []
        
    try:
        from database.connection import get_indices_collection, get_companies_collection
        indices_col = get_indices_collection()
        companies_col = get_companies_collection()
        
        regex_query = {"$regex": query, "$options": "i"}
        db_results = []
        
        # Match indices
        matched_indices = await indices_col.find({
            "$or": [{"symbol": regex_query}, {"name": regex_query}]
        }).to_list(length=5)
        
        for item in matched_indices:
            db_results.append({
                "symbol": item["symbol"],
                "name": item["name"],
                "logo": item.get("logo", ""),
                "sector": "Market Index",
                "exchDisp": item.get("exchDisp") or "Global Index",
                "typeDisp": "Index" if item["symbol"].startswith("^") else "Crypto"
            })
            
        # Match detailed seeded companies
        matched_companies = await companies_col.find({
            "$or": [{"symbol": regex_query}, {"name": regex_query}, {"sector_name": regex_query}]
        }).to_list(length=15)
        
        for item in matched_companies:
            db_results.append({
                "symbol": item["symbol"],
                "name": item["name"],
                "logo": item.get("logo", ""),
                "sector": item.get("sector_name", "Equity"),
                "exchDisp": item.get("exchDisp") or "NSE",
                "typeDisp": item.get("typeDisp") or "Equity"
            })
            
        if db_results:
            return db_results
            
        # If no DB hits, fallback to Yahoo API (auto-complete capability)
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                f"https://query2.finance.yahoo.com/v1/finance/search?q={query}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if res.status_code == 200:
                try:
                    res_data = res.json()
                    yahoo_quotes = res_data.get("quotes", [])
                except Exception as e:
                    logger.error(f"Failed to parse Yahoo autocomplete: {e}")
                    yahoo_quotes = []
                return [
                    {
                        "symbol": q["symbol"],
                        "name": q.get("shortname") or q.get("longname") or q["symbol"],
                        "logo": "",
                        "sector": q.get("quoteType") or "Asset",
                        "exchDisp": q.get("exchDisp") or "Exchange",
                        "typeDisp": q.get("quoteType") or "Equity"
                    }
                    for q in yahoo_quotes if "symbol" in q
                ]
        return []
    except Exception as e:
        logger.error(f"Ticker search autocomplete failed: {e}")
        return []

@router.post("/timeseries")
async def get_stock_timeseries(body: TimeseriesRequest):
    """
    Fetches OHLCV candlestick data from Yahoo Finance.
    Implements a 5-minute MongoDB cache to prevent rate limits.
    """
    symbol = body.symbol.upper().strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")
        
    try:
        from database.connection import Database, get_indices_collection, get_companies_collection
        db = Database.db
        cache_col = db["timeseries_cache"]
        
        # Check cache
        cache_key = f"{symbol}_{body.range}_{body.interval}"
        cached = await cache_col.find_one({"_id": cache_key})
        
        if cached:
            # Check if cache is younger than 5 minutes
            age = (datetime.utcnow() - cached["updated_at"]).total_seconds()
            if age < 300:
                return cached["data"]
        
        yahoo_symbol = symbol
        if yahoo_symbol in ["BTC", "ETH", "SOL"]:
            yahoo_symbol = f"{yahoo_symbol}-USD"
            
        indices_col = get_indices_collection()
        companies_col = get_companies_collection()
        db_info = await companies_col.find_one({"symbol": symbol})
        if not db_info:
            db_info = await indices_col.find_one({"symbol": symbol})
            
        name = db_info.get("name", symbol) if db_info else symbol
        logo = db_info.get("logo", "") if db_info else ""
        if yahoo_symbol in ["BTC", "ETH", "SOL"]:
            yahoo_symbol = f"{yahoo_symbol}-USD"
            
        query_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(yahoo_symbol)}?range={body.range}&interval={body.interval}"
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(query_url, headers={"User-Agent": "Mozilla/5.0"})
            if res.status_code != 200:
                logger.error(f"Yahoo Finance request failed for {yahoo_symbol} with {res.status_code}")
                return None
                
            json_data = res.json()
            if not json_data.get("chart") or not json_data.get("chart").get("result"):
                return None
                
            result = json_data["chart"]["result"][0]
            meta = result.get("meta", {})
            timestamps = result.get("timestamp", [])
            indicators = result.get("indicators", {})
            quote_data = indicators.get("quote", [{}])[0]
            
            opens = quote_data.get("open", [])
            highs = quote_data.get("high", [])
            lows = quote_data.get("low", [])
            closes = quote_data.get("close", [])
            volumes = quote_data.get("volume", [])
            
            points = []
            for i in range(min(len(timestamps), len(closes))):
                ts = timestamps[i]
                o = opens[i]
                h = highs[i]
                l = lows[i]
                c = closes[i]
                v = volumes[i] if i < len(volumes) else 0
                
                if c is not None and o is not None and h is not None and l is not None:
                    # lightweight-charts expects intraday time as unix timestamp in seconds
                    # and daily time as 'YYYY-MM-DD' string.
                    if body.interval in ["1d", "1wk", "1mo", "3mo"]:
                        formatted_time = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
                    else:
                        # For intraday, lightweight charts handles timezone better if we provide UTC time but configure the chart for local
                        # Wait, Yahoo returns timestamps in UTC. lightweight-charts expects UTC timestamp.
                        formatted_time = ts
                        
                    points.append({
                        "time": formatted_time,
                        "open": round(float(o), 2),
                        "high": round(float(h), 2),
                        "low": round(float(l), 2),
                        "close": round(float(c), 2),
                        "value": int(v) if v is not None else 0
                    })
                    
            latest_price = meta.get("regularMarketPrice")
            prev_close_price = meta.get("chartPreviousClose")
            
            if latest_price is None:
                latest_price = closes[-1] if closes else 0
            if prev_close_price is None:
                prev_close_price = closes[0] if closes else 0
                
            change = latest_price - prev_close_price
            change_percent = (change / prev_close_price) * 100 if prev_close_price else 0
            response_data = {
                "symbol": symbol,
                "name": name,
                "logo": logo,
                "price": latest_price,
                "change": change,
                "changePercent": change_percent,
                "marketCap": meta.get("marketCap"),
                "volume": meta.get("regularMarketVolume") or (volumes[-1] if volumes else None),
                "high52w": meta.get("fiftyTwoWeekHigh"),
                "low52w": meta.get("fiftyTwoWeekLow"),
                "points": points
            }
            
            # Cache the result
            await cache_col.update_one(
                {"_id": cache_key},
                {"$set": {"data": response_data, "updated_at": datetime.utcnow()}},
                upsert=True
            )
            
            return response_data
            
    except Exception as e:
        logger.error(f"Error fetching stock timeseries for {symbol}: {e}")
        return None

class QuotesRequest(BaseModel):
    symbols: list[str]

@router.post("/quotes")
async def get_quotes(body: QuotesRequest):
    """Fetch lightweight live quotes for a list of symbols (used for watchlists & heatmap)."""
    if not body.symbols:
        return []
        
    results = []
    
    try:
        from database.connection import get_indices_collection, get_companies_collection
        indices_col = get_indices_collection()
        companies_col = get_companies_collection()
        
        # Batch DB query for metadata
        all_db_items = await companies_col.find({"symbol": {"$in": body.symbols}}).to_list(length=100)
        index_db_items = await indices_col.find({"symbol": {"$in": body.symbols}}).to_list(length=100)
        all_assets = all_db_items + index_db_items
        
        # 1. Try Yahoo Finance batch quote endpoint - ultra fast, resolves in 1 request!
        try:
            symbols_str = ",".join(body.symbols)
            query_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols_str}"
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(query_url, headers={"User-Agent": "Mozilla/5.0"})
                if res.status_code == 200:
                    data = res.json()
                    quotes_list = data.get("quoteResponse", {}).get("result", [])
                    
                    for q in quotes_list:
                        sym = q["symbol"]
                        yahoo_price = q.get("regularMarketPrice", 0.0)
                        change_percent = q.get("regularMarketChangePercent", 0.0)
                        change = q.get("regularMarketChange", 0.0)
                        
                        db_info = next((a for a in all_assets if a["symbol"] == sym), {})
                        name = db_info.get("name") or q.get("shortName") or sym
                        
                        currency = q.get("currency", "USD")
                        if currency == "INR":
                            if yahoo_price is not None: yahoo_price /= 83.5
                            if change is not None: change /= 83.5
                            
                        results.append({
                           "symbol": sym,
                           "name": name,
                           "price": round(yahoo_price, 2) if yahoo_price else 0,
                           "change": round(change, 2) if change else 0,
                           "changePercent": round(change_percent, 2) if change_percent else 0,
                           "isPositive": change_percent >= 0 if change_percent is not None else True,
                           "history": []
                        })
        except Exception as batch_err:
            logger.error(f"Batch quotes fetch failed: {batch_err}. Falling back to parallel chart fetches.")
            
        # 2. Parallel fallback if batch returned nothing
        if not results:
            async def fetch_single_chart(client, sym):
                try:
                    from urllib.parse import quote
                    safe_sym = quote(sym)
                    res = await client.get(
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{safe_sym}",
                        headers={"User-Agent": "Mozilla/5.0"}
                    )
                    if res.status_code == 200:
                        data = res.json()
                        if data.get("chart") and data["chart"].get("result"):
                            meta = data["chart"]["result"][0]["meta"]
                            yahoo_price = meta.get("regularMarketPrice")
                            prev_close = meta.get("chartPreviousClose")
                            
                            db_info = next((a for a in all_assets if a["symbol"] == sym), {})
                            name = db_info.get("name") or meta.get("shortName") or sym
                            
                            currency = meta.get("currency")
                            if currency == "INR":
                                if yahoo_price is not None: yahoo_price /= 83.5
                                if prev_close is not None: prev_close /= 83.5
                                
                            change = (yahoo_price - prev_close) if yahoo_price and prev_close else 0
                            change_percent = (change / prev_close) * 100 if prev_close else 0
                            
                            return {
                                "symbol": sym,
                                "name": name,
                                "price": round(yahoo_price, 2) if yahoo_price else 0,
                                "change": round(change, 2),
                                "changePercent": round(change_percent, 2),
                                "isPositive": change >= 0,
                                "history": []
                            }
                except Exception as e:
                    logger.error(f"Error fetching parallel quote for {sym}: {e}")
                return None

            async with httpx.AsyncClient(timeout=8.0) as client:
                tasks = [fetch_single_chart(client, sym) for sym in body.symbols]
                completed = await asyncio.gather(*tasks)
                results = [r for r in completed if r is not None]
                
        return results
    except Exception as e:
        logger.error(f"Quotes fetch failed: {e}")
        return []


from tavily import AsyncTavilyClient

@router.get("/sentiment/{symbol}")
async def get_market_sentiment(symbol: str):
    try:
        tavily_keys_env = os.getenv("TAVILY_API_KEYS", "")
        tavily_keys = [k.strip() for k in tavily_keys_env.split(",") if k.strip()]
        if not tavily_keys:
            return {"sentiment": "Neutral", "sources": []}
            
        tavily_key = tavily_keys[0] # Just use the first one for simplicity, or cycle if needed
        client = AsyncTavilyClient(api_key=tavily_key)
        query = f"Current market sentiment and news for {symbol} stock"
        
        response = await client.search(
            query=query,
            search_depth="basic",
            max_results=6,
            include_answer=True
        )
        
        results = response.get("results", [])
        answer = response.get("answer", "")
        
        formatted_sources = []
        for r in results:
            formatted_sources.append({
                "title": r.get("title"),
                "url": r.get("url"),
                "content": r.get("content")
            })
            
        # Use Groq to analyze and summarize
        news_text = "\n\n".join([f"Title: {s['title']}\nContent: {s['content']}" for s in formatted_sources])
        
        system_prompt = """You are an expert financial analyst. Analyze the following recent news and market sentiment for the provided stock symbol.
Provide a comprehensive, multi-paragraph analysis in beautiful Markdown format. Use bolding and lists if appropriate.
You must output ONLY a valid JSON object matching this schema:
{
  "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "summary": "Your detailed markdown analysis here..."
}
"""
        user_prompt = f"Stock Symbol: {symbol}\n\nNews Data:\n{news_text}\n\nTavily Raw Summary: {answer}"
        
        llm_response = await query_groq(system_prompt, user_prompt)
        
        sentiment = "NEUTRAL"
        summary = answer or "No analysis available."
        
        if llm_response:
            try:
                import json
                parsed = json.loads(llm_response)
                sentiment = parsed.get("sentiment", "NEUTRAL").upper()
                summary = parsed.get("summary", summary)
            except Exception as e:
                logger.error(f"Error parsing LLM response: {e}")
                
        return {
            "sentiment": sentiment,
            "summary": summary,
            "sources": formatted_sources
        }
        
    except Exception as e:
        logger.error(f"Error fetching sentiment for {symbol}: {e}")
        return {"sentiment": "Neutral", "sources": [], "error": str(e)}

class SentimentRequest(BaseModel):
    query: str

@router.post("/sentiment")
async def get_market_sentiment_endpoint(req: SentimentRequest):
    tavily_key = get_tavily_key()
    if not tavily_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tavily API key is not configured."
        )
        
    logger.info(f"Fetching quick market sentiment list for query: {req.query}")
    
    # 1. Quick Tavily Search (no raw content, max 6 results, fast)
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": f"{req.query} stock market news",
                    "search_depth": "basic",
                    "include_raw_content": False,
                    "max_results": 6
                },
                timeout=10.0
            )
            
            if res.status_code != 200:
                logger.error(f"Tavily search failed with status {res.status_code}: {res.text}")
                raise HTTPException(status_code=502, detail="Tavily search API failed.")
                
            tavily_data = res.json()
            results = tavily_data.get("results", [])
    except Exception as e:
        logger.error(f"Tavily request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Tavily request failed: {str(e)}")
        
    if not results:
        return {
            "summary": "No recent market news or articles were found for this query.",
            "score": 50,
            "status": "Neutral",
            "articles": []
        }
        
    # 2. Compile quick articles payload (title + snippet)
    articles_payload = []
    for r in results:
        articles_payload.append({
            "title": r.get("title", "Unknown Source"),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:300]
        })
        
    # 3. Call Groq to summarize overall and classify article sentiments
    system_prompt = (
        "You are an expert market strategist and financial intelligence analyst.\n"
        "Analyze the overall market sentiment, key triggers, panic points, or bullish drivers based on the news snippets.\n"
        "Also, evaluate each article, determine its sentiment impact, and summarize its key arguments in 2 short sentences.\n"
        "Output ONLY a valid JSON object matching this schema exactly, with no additional markdown, markdown blocks, or text:\n"
        "{\n"
        "  \"summary\": \"A detailed, multi-paragraph analysis of the sentiment findings. Explain key market drivers, sector/company-specific issues, and macro outlook.\",\n"
        "  \"score\": 75, // Integer from 0 (Extreme Panic/Bearish) to 100 (Extreme Greed/Bullish)\n"
        "  \"status\": \"Greed\", // MUST be one of: \"Extreme Fear\", \"Fear\", \"Neutral\", \"Greed\", \"Extreme Greed\"\n"
        "  \"articles\": [\n"
        "    {\n"
        "      \"title\": \"Article Title\",\n"
        "      \"url\": \"Article URL\",\n"
        "      \"sentiment\": \"bullish\", // MUST be one of: \"bullish\", \"bearish\", \"neutral\"\n"
        "      \"summary\": \"A professional 2-sentence summary of the article's core points.\"\n"
        "    }\n"
        "  ]\n"
        "}"
    )
    
    user_prompt = f"Analyze the following stock market news results for the query: '{req.query}':\n\n" + json.dumps(articles_payload, indent=2)
    
    try:
        groq_raw = await query_groq(system_prompt, user_prompt, requested_model="llama-3.3-70b-versatile")
        
        # Clean up markdown
        if groq_raw.startswith("```"):
            lines = groq_raw.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            groq_raw = "\n".join(lines).strip()
            
        report = json.loads(groq_raw)
        
        # Inject raw snippet for frontend read detail
        for art in report.get("articles", []):
            match = next((r for r in results if r.get("url") == art.get("url")), None)
            if match:
                art["snippet"] = match.get("content", "")
                
        return report
    except Exception as e:
        logger.error(f"Groq parsing or execution failed: {e}")
        return {
            "summary": "An error occurred generating the detailed AI summary.",
            "score": 50,
            "status": "Neutral",
            "articles": [
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "sentiment": "neutral",
                    "summary": r.get("content", "")[:120],
                    "snippet": r.get("content", "")
                } for r in results
            ]
        }

class ArticleDetailRequest(BaseModel):
    url: str
    title: str

@router.post("/sentiment/article")
async def get_article_detail_endpoint(req: ArticleDetailRequest):
    tavily_key = get_tavily_key()
    if not tavily_key:
        raise HTTPException(status_code=500, detail="Tavily API key is not configured.")
        
    logger.info(f"Deep scraping and summarizing article: {req.url}")
    
    # 1. Search Tavily specifically for the URL and extract raw content
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": f"site:{req.url} or {req.title}",
                    "search_depth": "advanced",
                    "include_raw_content": True,
                    "max_results": 1
                },
                timeout=15.0
            )
            
            if res.status_code != 200:
                logger.error(f"Tavily search for article failed: {res.text}")
                raise HTTPException(status_code=502, detail="Tavily search API failed.")
                
            tavily_data = res.json()
            results = tavily_data.get("results", [])
    except Exception as e:
        logger.error(f"Tavily request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Tavily request failed: {str(e)}")
        
    raw_content = ""
    if results:
        raw_content = results[0].get("raw_content", results[0].get("content", ""))
        
    if not raw_content:
        return {
            "summary": "Unable to extract raw text content from the original website. Please visit the original site using the link below.",
            "raw_content": "No content could be extracted."
        }
        
    # 2. Call Groq to generate a high quality multi-paragraph summary of the raw content
    system_prompt = (
        "You are an expert financial journalist and research analyst.\n"
        "Your task is to review the raw scraped text content of the article and write a comprehensive, high-quality, professional multi-paragraph summary.\n"
        "Focus on key facts, data points, company actions, financial details, and market implications.\n"
        "Output ONLY a valid JSON object matching this schema exactly, with no additional markdown, markdown blocks, or text:\n"
        "{\n"
        "  \"summary\": \"A detailed, multi-paragraph comprehensive summary of the article's core details and financial data.\"\n"
        "}"
    )
    
    user_prompt = f"Scraped text content of article '{req.title}':\n\n{raw_content[:4000]}"
    
    try:
        groq_raw = await query_groq(system_prompt, user_prompt, requested_model="llama-3.3-70b-versatile")
        
        # Clean up markdown
        if groq_raw.startswith("```"):
            lines = groq_raw.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            groq_raw = "\n".join(lines).strip()
            
        report = json.loads(groq_raw)
        return {
            "summary": report.get("summary", "Summary generation failed."),
            "raw_content": raw_content
        }
    except Exception as e:
        logger.error(f"Groq article summary execution failed: {e}")
        return {
            "summary": "An error occurred while generating the detailed AI summary. Please read the raw scraped content snippet below.",
            "raw_content": raw_content
        }


@router.get("/heatmap")
async def get_market_heatmap():
    try:
        from database.connection import get_companies_collection
        companies_col = get_companies_collection()
        
        # Group companies by sector. Retrieve all sectors and all companies
        pipeline = [
            {"$match": {"sector_name": {"$ne": None, "$exists": True, "$ne": ""}}},
            {"$group": {
                "_id": "$sector_name",
                "companies": {
                    "$push": {
                        "symbol": "$symbol",
                        "name": "$name"
                    }
                }
            }},
            {"$project": {
                "sector": "$_id",
                "companies": "$companies"
            }}
        ]
        
        sectors_data = await companies_col.aggregate(pipeline).to_list(length=100)
        
        results = []
        for s in sectors_data:
            results.append({
                "sector": s["sector"],
                "companies": [{"symbol": c["symbol"], "name": c["name"]} for c in s["companies"]]
            })
            
        return results
    except Exception as e:
        logger.error(f"Failed to generate heatmap: {e}")
        raise HTTPException(status_code=500, detail=str(e))

import random
from datetime import datetime, timezone, timedelta
import hashlib
import asyncio
import json
from urllib.parse import quote
from fastapi import Query, Depends
from dependencies import get_current_user

@router.get("/recommendations")
async def get_market_recommendations(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1),
    refresh: bool = Query(False),
    current_user: dict = Depends(get_current_user)
):
    try:
        from database.connection import get_watchlist_collection, get_companies_collection, get_recommendations_cache_collection
        watchlist_col = get_watchlist_collection()
        companies_col = get_companies_collection()
        cache_col = get_recommendations_cache_collection()
        
        # 1. Fetch user's watchlist symbols
        watchlists = await watchlist_col.find({"user_email": current_user["email"]}).to_list(length=50)
        watchlist_symbols = set()
        for w in watchlists:
            for sym in w.get("symbols", []):
                watchlist_symbols.add(sym.upper())
                
        if not watchlist_symbols:
            return {"recommendations": [], "total": 0}
            
        watchlist_symbols = sorted(list(watchlist_symbols))
        total_symbols = len(watchlist_symbols)
        
        # 2. Paginate symbols on the backend
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_symbols = watchlist_symbols[start_idx:end_idx]
        
        if not paginated_symbols:
            return {"recommendations": [], "total": total_symbols}
            
        # 3. Fetch company names from DB
        companies_data = await companies_col.find({"symbol": {"$in": paginated_symbols}}).to_list(length=200)
        company_map = {c["symbol"]: c["name"] for c in companies_data}
        for sym in paginated_symbols:
            if sym not in company_map:
                company_map[sym] = sym.split(".")[0]
                
        current_hour = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H')
        real_quotes = {}
        
        # 4. Fetch live prices from Yahoo in parallel
        async def fetch_single_price(client, sym):
            try:
                safe_sym = quote(sym)
                res = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{safe_sym}",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                if res.status_code == 200:
                    data = res.json()
                    if data.get("chart") and data["chart"].get("result"):
                        meta = data["chart"]["result"][0]["meta"]
                        price = meta.get("regularMarketPrice")
                        prev_close = meta.get("chartPreviousClose")
                        change = (price - prev_close) if price and prev_close else 0
                        change_percent = (change / prev_close) * 100 if prev_close else 0
                        return sym, price, change, change_percent
            except Exception as e:
                pass
            return sym, None, None, None

        sem_yahoo = asyncio.Semaphore(10)
        async def bounded_fetch_price(client, sym):
            async with sem_yahoo:
                return await fetch_single_price(client, sym)
                
        async with httpx.AsyncClient(timeout=15.0) as client:
            tasks = [bounded_fetch_price(client, sym) for sym in paginated_symbols]
            completed_prices = await asyncio.gather(*tasks)
            for sym, price, change, change_percent in completed_prices:
                if price is not None:
                    real_quotes[sym] = {
                        "price": price,
                        "change": change,
                        "changePercent": change_percent
                    }
                    
        # 5. Caching Lookup (TTL = 10 Minutes)
        now = datetime.utcnow()
        cache_expiry_limit = now - timedelta(minutes=10)
        
        if refresh:
            cache_map = {}
        else:
            cached_docs = await cache_col.find({
                "symbol": {"$in": paginated_symbols},
                "last_updated": {"$gt": cache_expiry_limit}
            }).to_list(length=100)
            cache_map = {doc["symbol"]: doc for doc in cached_docs}
        
        # 6. Fetch AI sentiment for missing or expired cache symbols in parallel
        sem_ai = asyncio.Semaphore(3)
        ai_updates = {}
        
        async def scrape_and_analyze(sym):
            async with sem_ai:
                try:
                    comp_name = company_map[sym]
                    tavily_key = get_tavily_key()
                    if not tavily_key:
                        return None
                        
                    async with httpx.AsyncClient() as client:
                        res = await client.post(
                            "https://api.tavily.com/search",
                            json={
                                "api_key": tavily_key,
                                "query": f"\"{comp_name}\" or \"{sym}\" stock news market sentiment performance",
                                "search_depth": "basic",
                                "max_results": 5
                            },
                            timeout=15.0
                        )
                        if res.status_code != 200:
                            return None
                        tavily_data = res.json()
                        
                    news_results = tavily_data.get("results", [])
                    if not news_results:
                        return None
                        
                    # Include the article URL in the text so Groq can map it
                    news_text = "\n\n".join([
                        f"Source: {r.get('title')}\nLink URL: {r.get('url')}\nSnippet: {r.get('content')}" 
                        for r in news_results
                    ])
                    
                    sys_prompt = f"You are a professional equity research analyst. Analyze the following news reports for {comp_name} ({sym}).\nOutput ONLY a valid JSON object matching this schema exactly: {{\"score\": <int between 0 and 100>, \"action\": <one of 'STRONG BUY', 'BUY', 'HOLD', 'SELL', 'STRONG SELL'>, \"sources\": [{{\"source\": <short source name, e.g. CNBC, Reuters>, \"headline\": <summarized headline/point>, \"url\": <the exact Link URL from the snippet matched with this news item>}}]}}"
                    
                    llm_raw = await query_groq(sys_prompt, f"News for {sym}:\n{news_text}", "llama-3.3-70b-versatile")
                    if llm_raw:
                        if llm_raw.startswith("```"):
                            lines = llm_raw.splitlines()
                            if lines[0].startswith("```"): lines = lines[1:]
                            if lines[-1].startswith("```"): lines = lines[:-1]
                            llm_raw = "\n".join(lines).strip()
                        parsed = json.loads(llm_raw)
                        
                        # Save/Update in DB Cache
                        cache_doc = {
                            "symbol": sym,
                            "score": parsed.get("score", 50),
                            "action": parsed.get("action", "HOLD"),
                            "sources": parsed.get("sources", []),
                            "last_updated": datetime.utcnow()
                        }
                        await cache_col.update_one(
                            {"symbol": sym},
                            {"$set": cache_doc},
                            upsert=True
                        )
                        return sym, cache_doc
                except Exception as e:
                    logger.error(f"Failed to scrape AI sentiment for {sym}: {e}")
                return None

        scrape_tasks = []
        for sym in paginated_symbols:
            if sym not in cache_map:
                scrape_tasks.append(scrape_and_analyze(sym))
                
        if scrape_tasks:
            completed_scrapes = await asyncio.gather(*scrape_tasks)
            for item in completed_scrapes:
                if item is not None:
                    sym, doc = item
                    cache_map[sym] = doc
                    
        # 7. Compile the final paginated results
        ai_recommendations = []
        for sym in paginated_symbols:
            q = real_quotes.get(sym)
            if not q or q.get("price") is None:
                continue
                
            price = round(float(q["price"]), 2)
            change = round(float(q["change"]), 2)
            change_percent = round(float(q["changePercent"]), 2)
            
            base_sym = sym.split(".")[0]
            if sym.endswith(".NS"):
                logo = f"https://eodhd.com/img/logos/NSE/{base_sym}.png"
            elif sym.endswith(".BO"):
                logo = f"https://eodhd.com/img/logos/BSE/{base_sym}.png"
            else:
                logo = f"https://eodhd.com/img/logos/US/{sym}.png"
                
            cache_info = cache_map.get(sym, {})
            score = cache_info.get("score")
            action = cache_info.get("action")
            sources = cache_info.get("sources", [])
            
            has_real_news = False
            if cache_info and "last_updated" in cache_info:
                has_real_news = True
            else:
                # Mock fallback if both cache lookup and live scrape failed entirely
                momentum_adj = (change_percent * 0.5)
                score = min(58, max(42, int(50 + momentum_adj)))
                sources = [{"source": "Market News", "headline": f"Market Consensus: Neutral momentum of {change_percent}% detected.", "url": "https://finance.yahoo.com"}]
                
            if action not in ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]:
                if score >= 90:
                    action = "STRONG BUY"
                elif score >= 75:
                    action = "BUY"
                elif score <= 40:
                    action = "SELL"
                else:
                    action = "HOLD"
                    
            comp_seed = int(hashlib.md5(f"{current_hour}-{sym}".encode()).hexdigest()[:8], 16)
            c_rnd = random.Random(comp_seed)
            target = round(price * (1 + c_rnd.uniform(0.05, 0.15)), 2)
            stop = round(price * (1 - c_rnd.uniform(0.03, 0.08)), 2)
            
            ai_recommendations.append({
                "symbol": sym,
                "name": company_map[sym],
                "logo": logo,
                "price": price,
                "change": change,
                "changePercent": change_percent,
                "sentimentScore": score,
                "action": action,
                "targetPrice": target,
                "stopLoss": stop,
                "sources": sources,
                "hasRealNews": has_real_news
            })
            
        # Sort paginated results by sentiment score
        sorted_recommendations = sorted(ai_recommendations, key=lambda x: x["sentimentScore"], reverse=True)
        return {"recommendations": sorted_recommendations, "total": total_symbols}
        
    except Exception as e:
        logger.error(f"Failed to fetch watchlist AI recommendations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommendations/email")
async def trigger_watchlist_recommendations_email(current_user: dict = Depends(get_current_user)):
    try:
        from services.recommendations import generate_watchlist_recs
        from services.email import send_watchlist_sentiment_email
        
        email = current_user["email"]
        recs = await generate_watchlist_recs(email)
        
        if not recs:
            raise HTTPException(status_code=400, detail="Cannot send report: Watchlist is empty.")
            
        success = send_watchlist_sentiment_email(email, recs)
        if success:
            return {"message": "AI Sentiment report dispatched successfully."}
        else:
            raise HTTPException(status_code=500, detail="Failed to transmit email report.")
    except Exception as e:
        logger.error(f"Manual watchlist email trigger failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recommendations/email/cron")
async def trigger_cron_emails(request: Request):
    import os
    auth_header = request.headers.get("Authorization")
    expected_secret = os.getenv("CRON_SECRET")
    if expected_secret:
        if not auth_header or auth_header != f"Bearer {expected_secret}":
            raise HTTPException(status_code=403, detail="Forbidden")
    from services.recommendations import dispatch_watchlist_emails_to_all_users
    await dispatch_watchlist_emails_to_all_users()
    return {"status": "emails dispatched"}
