import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database.connection import Database
from routes import auth, onboarding, market, watchlist, ai, paper

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Establish connection pooling to MongoDB on server startup
    await Database.connect_db()
    
    # Check if database is empty, seed if needed
    from database.connection import get_indices_collection
    indices_col = get_indices_collection()
    count = await indices_col.count_documents({})
    if count == 0:
        try:
            from seed_indian_stocks import main as seed_main
            await seed_main()
            print("[Equinox Server] Successfully seeded relational indices, sectors, and companies!")
        except Exception as e:
            print(f"[Equinox Server] Error running database seeder: {e}")
            
    # Start the watchlist email scheduler background task
    import asyncio
    from services.recommendations import start_email_scheduler
    from services.pinger import start_render_pinger
    
    scheduler_task = asyncio.create_task(start_email_scheduler())
    pinger_task = asyncio.create_task(start_render_pinger())
            
    yield
    # Cancel background scheduler task and pinger task
    scheduler_task.cancel()
    pinger_task.cancel()
    # Close database pool on server shutdown
    await Database.close_db()

app = FastAPI(
    title="Equinox API Server",
    description="Backend services for the Equinox Stock Analytics Engine (MongoDB & JWT Auth)",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS Middleware to allow seamless integration with frontend client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register route controllers
app.include_router(auth.router)
app.include_router(onboarding.router)
app.include_router(market.router)
app.include_router(watchlist.router)
app.include_router(ai.router)
app.include_router(paper.router)

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "Equinox API Server",
        "version": "1.0.0",
        "docs_url": "/docs"
    }

@app.get("/health")
@app.get("/api/v1/health")
def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
