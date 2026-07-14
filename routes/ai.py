from fastapi import APIRouter, HTTPException, Depends
from typing import List
from datetime import datetime
from bson import ObjectId

from database.connection import Database
from models.ai import ChatSession, ChatSessionCreate, ChatMessage, ChatMessageCreate
from dependencies import get_current_user
from services.ai_agent import invoke_agent
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/ai", tags=["AI"])

class SessionUpdate(BaseModel):
    title: str

class ChatRequestStateless(BaseModel):
    message: str
    history: list = []
    context: dict = None
    model: str = "Llama 3 70B"

@router.post("/chat")
async def stateless_chat(req: ChatRequestStateless):
    # Combine history with the new message
    history = req.history + [{"role": "user", "content": req.message}]
    symbol = req.context.get("symbol") if req.context else None
    
    try:
        ai_content, sources = invoke_agent(history, context_symbol=symbol, model_name=req.model)
        return {"message": ai_content, "sources": sources}
    except Exception as e:
        return {"message": f"Error communicating with AI: {str(e)}", "sources": []}

@router.post("/sessions", response_model=ChatSession)
async def create_session(session_data: ChatSessionCreate, current_user: dict = Depends(get_current_user)):
    db = Database.db
    session = {
        "user_id": str(current_user["_id"]),
        "title": session_data.title,
        "is_shared": session_data.is_shared,
        "context_symbol": session_data.context_symbol,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = await db["chat_sessions"].insert_one(session)
    session["_id"] = str(result.inserted_id)
    return session

@router.get("/sessions", response_model=List[ChatSession])
async def list_sessions(current_user: dict = Depends(get_current_user)):
    db = Database.db
    sessions = await db["chat_sessions"].find({"user_id": str(current_user["_id"])}).sort("updated_at", -1).to_list(100)
    for s in sessions:
        s["_id"] = str(s["_id"])
    return sessions

@router.get("/sessions/{session_id}", response_model=dict)
async def get_session(session_id: str, current_user: dict = Depends(get_current_user)):
    db = Database.db
    session = await db["chat_sessions"].find_one({"_id": ObjectId(session_id)})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if session["user_id"] != str(current_user["_id"]) and not session.get("is_shared", False):
        raise HTTPException(status_code=403, detail="Not authorized to view this session")
        
    session["_id"] = str(session["_id"])
    
    messages = await db["chat_messages"].find({"session_id": session_id}).sort("created_at", 1).to_list(1000)
    for m in messages:
        m["_id"] = str(m["_id"])
        
    return {"session": session, "messages": messages}

@router.put("/sessions/{session_id}")
async def update_session(session_id: str, update_data: SessionUpdate, current_user: dict = Depends(get_current_user)):
    db = Database.db
    session = await db["chat_sessions"].find_one({"_id": ObjectId(session_id), "user_id": str(current_user["_id"])})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")
        
    await db["chat_sessions"].update_one(
        {"_id": ObjectId(session_id)},
        {"$set": {"title": update_data.title, "updated_at": datetime.utcnow()}}
    )
    return {"message": "Session updated successfully"}

@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    db = Database.db
    session = await db["chat_sessions"].find_one({"_id": ObjectId(session_id), "user_id": str(current_user["_id"])})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")
        
    await db["chat_sessions"].delete_one({"_id": ObjectId(session_id)})
    await db["chat_messages"].delete_many({"session_id": session_id})
    return {"message": "Session deleted successfully"}

@router.post("/sessions/{session_id}/message", response_model=ChatMessage)
async def send_message(session_id: str, message_data: ChatMessageCreate, current_user: dict = Depends(get_current_user)):
    db = Database.db
    session = await db["chat_sessions"].find_one({"_id": ObjectId(session_id), "user_id": str(current_user["_id"])})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")

    # 1. Save user message
    user_msg = {
        "session_id": session_id,
        "role": "user",
        "content": message_data.content,
        "sources": [],
        "created_at": datetime.utcnow()
    }
    await db["chat_messages"].insert_one(user_msg)

    # 2. Fetch history
    history_docs = await db["chat_messages"].find({"session_id": session_id}).sort("created_at", 1).to_list(100)
    history = [{"role": doc["role"], "content": doc["content"]} for doc in history_docs]

    # 3. Invoke LangGraph agent
    try:
        ai_content, sources = invoke_agent(history, context_symbol=session.get("context_symbol"), context_data=message_data.context)
    except Exception as e:
        ai_content = f"An error occurred while generating a response: {str(e)}"
        sources = []

    # 4. Save AI message
    ai_msg = {
        "session_id": session_id,
        "role": "ai",
        "content": ai_content,
        "sources": sources,
        "created_at": datetime.utcnow()
    }
    
    result = await db["chat_messages"].insert_one(ai_msg)
    ai_msg["_id"] = str(result.inserted_id)
    
    # Update session updated_at and optionally title if it's the first message
    update_data = {"updated_at": datetime.utcnow()}
    if len(history_docs) <= 2: # First back and forth
        update_data["title"] = message_data.content[:40] + ("..." if len(message_data.content) > 40 else "")
    
    await db["chat_sessions"].update_one({"_id": ObjectId(session_id)}, {"$set": update_data})

    return ai_msg

@router.put("/sessions/{session_id}/share")
async def share_session_endpoint(session_id: str, current_user: dict = Depends(get_current_user)):
    db = Database.db
    session = await db["chat_sessions"].find_one({"_id": ObjectId(session_id), "user_id": str(current_user["_id"])})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or unauthorized")
    await db["chat_sessions"].update_one({"_id": ObjectId(session_id)}, {"$set": {"is_shared": True}})
    return {"message": "Session marked as shared"}

@router.post("/sessions/{session_id}/clone")
async def clone_session(session_id: str, current_user: dict = Depends(get_current_user)):
    db = Database.db
    session = await db["chat_sessions"].find_one({"_id": ObjectId(session_id)})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    # Create cloned session
    cloned_session = {
        "user_id": str(current_user["_id"]),
        "title": f"Cloned: {session.get('title', 'Chat')}",
        "is_shared": False,
        "context_symbol": session.get("context_symbol"),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    result = await db["chat_sessions"].insert_one(cloned_session)
    cloned_id = str(result.inserted_id)
    
    # Clone all messages
    messages = await db["chat_messages"].find({"session_id": session_id}).sort("created_at", 1).to_list(1000)
    cloned_messages = []
    for m in messages:
        cloned_msg = {
            "session_id": cloned_id,
            "role": m["role"],
            "content": m["content"],
            "sources": m.get("sources", []),
            "created_at": datetime.utcnow() # fresh timestamps for order
        }
        cloned_messages.append(cloned_msg)
        
    if cloned_messages:
        await db["chat_messages"].insert_many(cloned_messages)
        
    return {"session_id": cloned_id}
