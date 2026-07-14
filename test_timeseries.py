import asyncio
import httpx

async def test():
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "http://localhost:8000/api/v1/market/timeseries",
            json={"symbol": "WOCKPHARMA.NS", "range": "1mo", "interval": "1d"}
        )
        print(res.status_code)
        print(res.text)

asyncio.run(test())
