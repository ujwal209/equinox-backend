import asyncio
from database.connection import Database
from routes.market import get_stock_timeseries, TimeseriesRequest

async def test():
    await Database.connect_db()
    req = TimeseriesRequest(symbol="WOCKPHARMA.NS", range="1mo", interval="1d")
    res = await get_stock_timeseries(req)
    print("Result Keys:", res.keys() if res else None)
    await Database.close_db()

asyncio.run(test())
