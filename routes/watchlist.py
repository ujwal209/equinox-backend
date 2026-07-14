from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List
from datetime import datetime
from bson import ObjectId
from database.connection import get_watchlist_collection
from dependencies import get_current_user
import logging

router = APIRouter(prefix="/api/v1/watchlist", tags=["Watchlist"])
logger = logging.getLogger("uvicorn.error")

class WatchlistCreate(BaseModel):
    name: str

class SymbolRequest(BaseModel):
    symbol: str

class WatchlistResponse(BaseModel):
    id: str
    name: str
    symbols: List[str]
    created_at: datetime

def format_watchlist(doc) -> WatchlistResponse:
    return WatchlistResponse(
        id=str(doc["_id"]),
        name=doc["name"],
        symbols=doc.get("symbols", []),
        created_at=doc["created_at"]
    )

@router.get("/", response_model=List[WatchlistResponse])
async def get_watchlists(current_user: dict = Depends(get_current_user)):
    collection = get_watchlist_collection()
    cursor = collection.find({"user_email": current_user["email"]}).sort("created_at", 1)
    watchlists = await cursor.to_list(length=50)
    
    if len(watchlists) == 0:
        defaults = ["Watchlist 1", "Watchlist 2", "Watchlist 3", "Watchlist 4", "Watchlist 5"]
        new_docs = []
        for name in defaults:
            new_docs.append({
                "user_email": current_user["email"],
                "name": name,
                "symbols": [],
                "created_at": datetime.utcnow()
            })
        if new_docs:
            result = await collection.insert_many(new_docs)
            for i, _id in enumerate(result.inserted_ids):
                new_docs[i]["_id"] = _id
            watchlists = new_docs
            
    return [format_watchlist(w) for w in watchlists]

@router.post("/", response_model=WatchlistResponse)
async def create_watchlist(schema: WatchlistCreate, current_user: dict = Depends(get_current_user)):
    collection = get_watchlist_collection()
    
    # Check limit (e.g. max 10 watchlists per user)
    count = await collection.count_documents({"user_email": current_user["email"]})
    if count >= 10:
        raise HTTPException(status_code=400, detail="Maximum number of watchlists reached.")
        
    doc = {
        "user_email": current_user["email"],
        "name": schema.name.strip(),
        "symbols": [],
        "created_at": datetime.utcnow()
    }
    
    result = await collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return format_watchlist(doc)

@router.delete("/{watchlist_id}")
async def delete_watchlist(watchlist_id: str, current_user: dict = Depends(get_current_user)):
    collection = get_watchlist_collection()
    try:
        obj_id = ObjectId(watchlist_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid watchlist ID.")
        
    result = await collection.delete_one({"_id": obj_id, "user_email": current_user["email"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Watchlist not found.")
    return {"message": "Watchlist deleted successfully"}

@router.post("/{watchlist_id}/add", response_model=WatchlistResponse)
async def add_symbol(watchlist_id: str, schema: SymbolRequest, current_user: dict = Depends(get_current_user)):
    collection = get_watchlist_collection()
    symbol = schema.symbol.upper().strip()
    
    try:
        obj_id = ObjectId(watchlist_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid watchlist ID.")
        
    watchlist = await collection.find_one({"_id": obj_id, "user_email": current_user["email"]})
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found.")
        
    if symbol not in watchlist.get("symbols", []):
        await collection.update_one(
            {"_id": obj_id},
            {"$push": {"symbols": symbol}}
        )
        watchlist["symbols"].append(symbol)
        
    return format_watchlist(watchlist)

@router.delete("/{watchlist_id}/remove/{symbol}", response_model=WatchlistResponse)
async def remove_symbol(watchlist_id: str, symbol: str, current_user: dict = Depends(get_current_user)):
    collection = get_watchlist_collection()
    symbol = symbol.upper().strip()
    
    try:
        obj_id = ObjectId(watchlist_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid watchlist ID.")
        
    watchlist = await collection.find_one({"_id": obj_id, "user_email": current_user["email"]})
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found.")
        
    if symbol in watchlist.get("symbols", []):
        await collection.update_one(
            {"_id": obj_id},
            {"$pull": {"symbols": symbol}}
        )
        watchlist["symbols"].remove(symbol)
        
    return format_watchlist(watchlist)
