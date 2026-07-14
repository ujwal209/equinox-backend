from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional
from datetime import datetime

# Auth Request Schemas
class UserSignup(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, description="Password must be at least 6 characters.")

class UserVerifyOTP(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6, description="6-digit verification code.")

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserForgotPassword(BaseModel):
    email: EmailStr

class UserVerifyResetPassword(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=6)

# User Onboarding Profile Schema
class OnboardingSchema(BaseModel):
    experience_years: str = Field(..., description="e.g. Beginner, Intermediate, Expert")
    risk_tolerance: str = Field(..., description="e.g. Low, Medium, High")
    preferred_assets: List[str] = Field(..., description="e.g. ['Equities', 'Crypto', 'Options']")
    trading_budget: float = Field(..., gt=0, description="Trading budget amount.")

# response details
class OnboardingResponse(OnboardingSchema):
    onboarded: bool = True

class UserResponse(BaseModel):
    id: str
    email: EmailStr
    is_verified: bool
    onboarded: bool
    onboarding: Optional[OnboardingResponse] = None
    created_at: datetime

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
