from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from database.connection import get_user_collection
from services.security import decode_access_token

# Initialize OAuth2 security scheme pointing to login endpoint
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization session token is missing. Please login."
        )
    
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token is invalid or has expired."
        )
    
    users = get_user_collection()
    user = await users.find_one({"email": payload["sub"]})
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user account could not be found."
        )
    return user
