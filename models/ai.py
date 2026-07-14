from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from datetime import datetime

class ChatSessionCreate(BaseModel):
    title: str = "New Session"
    is_shared: bool = False
    context_symbol: Optional[str] = None # e.g. "AAPL" if created from stock page

class ChatSession(BaseModel):
    id: str = Field(alias="_id")
    user_id: str
    title: str
    is_shared: bool
    context_symbol: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class ChatMessage(BaseModel):
    id: str = Field(alias="_id")
    session_id: str
    role: str # 'user' or 'ai'
    content: str
    context: Optional[Dict[str, Any]] = None
    sources: Optional[List[Dict[str, Any]]] = []
    created_at: datetime

class ChatMessageCreate(BaseModel):
    content: str
    context: Optional[Dict[str, Any]] = None
