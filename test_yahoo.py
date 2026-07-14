import asyncio
import httpx
from urllib.parse import quote

async def test():
    symbol = "WOCKPHARMA.NS"
    range_val = "1mo"
    interval = "1d"
    query_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range={range_val}&interval={interval}"
    
    async with httpx.AsyncClient() as client:
        res = await client.get(query_url, headers={"User-Agent": "Mozilla/5.0"})
        print(f"Status Code: {res.status_code}")
        
        if res.status_code != 200:
            print("Failed.")
            return
            
        json_data = res.json()
        
        if not json_data.get("chart") or not json_data.get("chart").get("result"):
            print("No chart result")
            return
            
        result = json_data["chart"]["result"][0]
        meta = result.get("meta", {})
        timestamps = result.get("timestamp", [])
        indicators = result.get("indicators", {})
        quote_data = indicators.get("quote", [{}])[0]
        
        opens = quote_data.get("open", [])
        closes = quote_data.get("close", [])
        volumes = quote_data.get("volume", [])
        
        print(f"Meta currency: {meta.get('currency')}")
        print(f"Timestamps: {len(timestamps)}")
        print(f"Closes: {len(closes)}")
        
        c = closes[0]
        print(f"c is {c}, type: {type(c)}")

asyncio.run(test())
