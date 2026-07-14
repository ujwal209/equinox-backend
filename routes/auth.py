import random
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, status
from models.user import (
    UserSignup, UserVerifyOTP, UserLogin, 
    UserForgotPassword, UserVerifyResetPassword,
    UserResponse, TokenResponse
)
from database.connection import get_user_collection, get_otp_collection
from services.security import hash_password, verify_password, create_access_token
from services.email import send_otp_email

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])
logger = logging.getLogger("uvicorn.error")

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(schema: UserSignup):
    users = get_user_collection()
    otps = get_otp_collection()

    # Check if user already exists
    existing_user = await users.find_one({"email": schema.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address already exists."
        )

    # Hash user password
    pwd_hash = hash_password(schema.password)

    new_user = {
        "email": schema.email,
        "password_hash": pwd_hash,
        "is_verified": False,
        "onboarded": False,
        "onboarding": None,
        "created_at": datetime.utcnow()
    }

    # Insert user into database
    await users.insert_one(new_user)

    # Generate 6-digit OTP
    otp_code = f"{random.randint(100000, 999999)}"
    expires_at = datetime.utcnow() + timedelta(minutes=5)

    otp_doc = {
        "email": schema.email,
        "otp": otp_code,
        "purpose": "signup",
        "expires_at": expires_at
    }

    # Save OTP to database (upsert to overwrite older codes)
    await otps.update_one(
        {"email": schema.email, "purpose": "signup"},
        {"$set": otp_doc},
        upsert=True
    )

    # Send verification email
    send_otp_email(schema.email, otp_code, purpose="signup")

    return {"message": "User registered successfully. Verification OTP code sent to email."}


@router.post("/verify-signup")
async def verify_signup(schema: UserVerifyOTP):
    users = get_user_collection()
    otps = get_otp_collection()

    # Locate the active OTP doc
    otp_doc = await otps.find_one({
        "email": schema.email,
        "otp": schema.otp,
        "purpose": "signup"
    })

    if not otp_doc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or incorrect verification code."
        )

    # Verify if code is expired
    if datetime.utcnow() > otp_doc["expires_at"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code has expired."
        )

    # Update user verified status in DB
    await users.update_one(
        {"email": schema.email},
        {"$set": {"is_verified": True}}
    )

    # Delete used OTP
    await otps.delete_one({"_id": otp_doc["_id"]})

    return {"message": "Email verified successfully. You can now login."}


@router.post("/login", response_model=TokenResponse)
async def login(schema: UserLogin):
    users = get_user_collection()

    user = await users.find_one({"email": schema.email})
    if not user or not verify_password(schema.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email address or password credentials."
        )

    # Require email verification prior to login
    if not user["is_verified"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before logging in."
        )

    # Generate JWT Session token
    access_token = create_access_token(data={"sub": user["email"]})
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/forgot-password")
async def forgot_password(schema: UserForgotPassword):
    users = get_user_collection()
    otps = get_otp_collection()

    user = await users.find_one({"email": schema.email})
    if not user:
        # Standard safety: Do not disclose whether email exists
        return {"message": "If the account exists, a password reset OTP code has been dispatched."}

    # Generate password reset OTP
    otp_code = f"{random.randint(100000, 999999)}"
    expires_at = datetime.utcnow() + timedelta(minutes=5)

    otp_doc = {
        "email": schema.email,
        "otp": otp_code,
        "purpose": "reset",
        "expires_at": expires_at
    }

    # Save OTP to database
    await otps.update_one(
        {"email": schema.email, "purpose": "reset"},
        {"$set": otp_doc},
        upsert=True
    )

    # Send reset email
    send_otp_email(schema.email, otp_code, purpose="reset")

    return {"message": "If the account exists, a password reset OTP code has been dispatched."}


@router.post("/reset-password")
async def reset_password(schema: UserVerifyResetPassword):
    users = get_user_collection()
    otps = get_otp_collection()

    # Check OTP correctness
    otp_doc = await otps.find_one({
        "email": schema.email,
        "otp": schema.otp,
        "purpose": "reset"
    })

    if not otp_doc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or incorrect password reset code."
        )

    # Check OTP expiration
    if datetime.utcnow() > otp_doc["expires_at"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset code has expired."
        )

    # Hash new password
    new_pwd_hash = hash_password(schema.new_password)

    # Update user password in DB
    await users.update_one(
        {"email": schema.email},
        {"$set": {"password_hash": new_pwd_hash}}
    )

    # Delete used OTP
    await otps.delete_one({"_id": otp_doc["_id"]})

    return {"message": "Password updated successfully. You can now login using your new credentials."}
