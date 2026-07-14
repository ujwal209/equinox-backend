import logging
from motor.motor_asyncio import AsyncIOMotorClient
from config.settings import settings

logger = logging.getLogger("uvicorn.error")

class Database:
    client: AsyncIOMotorClient = None
    db = None

    @classmethod
    async def connect_db(cls):
        logger.info(f"Connecting to MongoDB database context...")
        try:
            cls.client = AsyncIOMotorClient(settings.MONGO_URI)
            # Pick DB name from URI path or default to 'equinox'
            db_name = settings.MONGO_URI.split("/")[-1].split("?")[0] or "equinox"
            cls.db = cls.client[db_name]
            logger.info(f"Successfully connected to MongoDB database: '{db_name}'")
        except Exception as e:
            logger.error(f"Failed to establish MongoDB client session: {e}")
            raise e

    @classmethod
    async def close_db(cls):
        if cls.client:
            cls.client.close()
            logger.info("Closed MongoDB database connection client pool.")

# Helper to fetch active DB collections
def get_user_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["users"]

def get_otp_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["otps"]

def get_indices_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["indices"]

def get_sectors_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["sectors"]

def get_watchlist_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["watchlists"]

def get_companies_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["companies"]


def get_paper_portfolios_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["paper_portfolios"]

def get_paper_positions_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["paper_positions"]

def get_paper_orders_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["paper_orders"]

def get_recommendations_cache_collection():
    if Database.db is None:
        raise RuntimeError("Database connection not initialized.")
    return Database.db["recommendations_cache"]

