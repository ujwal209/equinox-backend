import os
from dotenv import load_dotenv

# Load environment variables from backend/.env
load_dotenv()

def get_clean_env(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if val is not None:
        return val.strip()
    return default

class Settings:
    # Database (supports MONGODB_URI or MONGO_URI)
    MONGO_URI: str = get_clean_env("MONGODB_URI") or get_clean_env("MONGO_URI") or "mongodb://localhost:27017/equinox"

    # JWT
    JWT_SECRET: str = get_clean_env("JWT_SECRET", "equinox_default_secret_key_change_me")
    JWT_EXPIRATION_MINUTES: int = int(get_clean_env("JWT_EXPIRATION_MINUTES", "10080"))

    # SMTP Configuration (mapped to USER's exact env keys: EMAIL_WORKER & APP_PASSWORD)
    SMTP_HOST: str = get_clean_env("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(get_clean_env("SMTP_PORT", "587"))
    SMTP_USERNAME: str = get_clean_env("EMAIL_WORKER") or get_clean_env("SMTP_USERNAME") or "your-email@gmail.com"
    SMTP_PASSWORD: str = get_clean_env("APP_PASSWORD") or get_clean_env("SMTP_PASSWORD") or "your-app-password"
    SMTP_SENDER: str = get_clean_env("EMAIL_WORKER") or get_clean_env("SMTP_SENDER") or "your-email@gmail.com"

settings = Settings()
