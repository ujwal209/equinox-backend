from fastapi import APIRouter, Depends, HTTPException, status
from models.user import OnboardingSchema, UserResponse, OnboardingResponse
from database.connection import get_user_collection
from dependencies import get_current_user
from bson import ObjectId

router = APIRouter(prefix="/api/v1/user", tags=["User Profile"])



# Helper to format mongo document to Pydantic UserResponse schema
def format_user_doc(user_doc: dict) -> UserResponse:
    # Convert ObjectId to string
    user_id = str(user_doc["_id"])
    
    onboarding_data = None
    if user_doc.get("onboarding"):
        onboarding_data = OnboardingResponse(
            experience_years=user_doc["onboarding"]["experience_years"],
            risk_tolerance=user_doc["onboarding"]["risk_tolerance"],
            preferred_assets=user_doc["onboarding"]["preferred_assets"],
            trading_budget=user_doc["onboarding"]["trading_budget"],
            onboarded=user_doc["onboarding"].get("onboarded", True)
        )

    return UserResponse(
        id=user_id,
        email=user_doc["email"],
        is_verified=user_doc["is_verified"],
        onboarded=user_doc["onboarded"],
        onboarding=onboarding_data,
        created_at=user_doc["created_at"]
    )

@router.post("/onboarding", response_model=UserResponse)
async def submit_onboarding(schema: OnboardingSchema, current_user: dict = Depends(get_current_user)):
    users = get_user_collection()

    onboarding_doc = {
        "experience_years": schema.experience_years,
        "risk_tolerance": schema.risk_tolerance,
        "preferred_assets": schema.preferred_assets,
        "trading_budget": schema.trading_budget,
        "onboarded": True
    }

    # Update user document with onboarding settings
    await users.update_one(
        {"_id": current_user["_id"]},
        {"$set": {
            "onboarding": onboarding_doc,
            "onboarded": True
        }}
    )

    # Fetch updated user profile
    updated_user = await users.find_one({"_id": current_user["_id"]})
    return format_user_doc(updated_user)

@router.post("/onboarding/skip", response_model=UserResponse)
async def skip_onboarding(current_user: dict = Depends(get_current_user)):
    users = get_user_collection()
    
    await users.update_one(
        {"_id": current_user["_id"]},
        {"$set": {
            "onboarded": True,
            "onboarding": None
        }}
    )
    
    updated_user = await users.find_one({"_id": current_user["_id"]})
    return format_user_doc(updated_user)

@router.get("/me", response_model=UserResponse)
async def get_my_profile(current_user: dict = Depends(get_current_user)):
    return format_user_doc(current_user)
