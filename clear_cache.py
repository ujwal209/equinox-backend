import asyncio
from database.connection import Database

async def clear_cache():
    await Database.connect_db()
    db = Database.db
    if db is not None:
        result = await db["timeseries_cache"].delete_many({})
        print(f"Cleared {result.deleted_count} cached documents.")
    await Database.close_db()

if __name__ == "__main__":
    asyncio.run(clear_cache())
