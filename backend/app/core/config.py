import os
import logging
import secrets
from urllib.parse import urlparse
from dotenv import load_dotenv
load_dotenv()

auth_secret_key = os.getenv("AUTH_SECRET_KEY")
if auth_secret_key:
    if len(auth_secret_key) < 32 or auth_secret_key.startswith("replace-with-"):
        raise ValueError(
            "Set AUTH_SECRET_KEY to a non-placeholder value of at least 32 characters"
        )
else:
    auth_secret_key = secrets.token_urlsafe(32)
    logging.warning(
        "AUTH_SECRET_KEY is not set; using a temporary key. "
        "Set a persistent key to preserve sessions across restarts."
    )

class Settings:
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", os.getenv("GEMINI_API_KEY", ""))
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")  # gemini or openai
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./docs_meta.db")
    AUTH_SECRET_KEY = auth_secret_key
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))
    AUTH_REGISTER_LIMIT = int(os.getenv("AUTH_REGISTER_LIMIT", "5"))
    AUTH_REGISTER_WINDOW_SECONDS = int(
        os.getenv("AUTH_REGISTER_WINDOW_SECONDS", "3600")
    )
    AUTH_LOGIN_LIMIT = int(os.getenv("AUTH_LOGIN_LIMIT", "10"))
    AUTH_LOGIN_WINDOW_SECONDS = int(os.getenv("AUTH_LOGIN_WINDOW_SECONDS", "60"))
    AUTH_RECOVERY_LIMIT = int(os.getenv("AUTH_RECOVERY_LIMIT", "5"))
    AUTH_RECOVERY_WINDOW_SECONDS = int(
        os.getenv("AUTH_RECOVERY_WINDOW_SECONDS", "3600")
    )
    EMAIL_TOKEN_EXPIRE_MINUTES = int(os.getenv("EMAIL_TOKEN_EXPIRE_MINUTES", "30"))
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = int(
        os.getenv("PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", "30")
    )
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "")
    SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "true").lower() == "true"
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    ]
    MAX_DOCS = int(os.getenv("MAX_DOCS", "30"))
    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
    GUEST_SESSION_TTL_HOURS = int(os.getenv("GUEST_SESSION_TTL_HOURS", "24"))
    GUEST_SESSION_LIMIT = int(os.getenv("GUEST_SESSION_LIMIT", "10"))
    GUEST_SESSION_WINDOW_SECONDS = int(
        os.getenv("GUEST_SESSION_WINDOW_SECONDS", "3600")
    )
    GUEST_MAX_DOCS = int(os.getenv("GUEST_MAX_DOCS", "3"))
    GUEST_MAX_FILE_SIZE_MB = int(os.getenv("GUEST_MAX_FILE_SIZE_MB", "5"))
    GUEST_MAX_PAGES = int(os.getenv("GUEST_MAX_PAGES", "100"))
    GUEST_MAX_EXTRACTED_CHARS = int(
        os.getenv("GUEST_MAX_EXTRACTED_CHARS", "1000000")
    )
    GUEST_UPLOAD_LIMIT = int(os.getenv("GUEST_UPLOAD_LIMIT", "5"))
    GUEST_UPLOAD_WINDOW_SECONDS = int(
        os.getenv("GUEST_UPLOAD_WINDOW_SECONDS", "3600")
    )
    GUEST_QUERY_LIMIT = int(os.getenv("GUEST_QUERY_LIMIT", "20"))
    GUEST_QUERY_WINDOW_SECONDS = int(
        os.getenv("GUEST_QUERY_WINDOW_SECONDS", "3600")
    )
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
    CHUNK_SIZE = 1000
    CHUNK_OVERLAP = 150
    TOP_K = 5

settings = Settings()
if settings.ACCESS_TOKEN_EXPIRE_MINUTES < 1:
    raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must be at least 1")
if min(
    settings.AUTH_REGISTER_LIMIT,
    settings.AUTH_REGISTER_WINDOW_SECONDS,
    settings.AUTH_LOGIN_LIMIT,
    settings.AUTH_LOGIN_WINDOW_SECONDS,
    settings.AUTH_RECOVERY_LIMIT,
    settings.AUTH_RECOVERY_WINDOW_SECONDS,
    settings.EMAIL_TOKEN_EXPIRE_MINUTES,
    settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
) < 1:
    raise ValueError("Authentication rate limits and windows must be positive")
if min(
    settings.GUEST_SESSION_TTL_HOURS,
    settings.GUEST_SESSION_LIMIT,
    settings.GUEST_SESSION_WINDOW_SECONDS,
    settings.GUEST_MAX_DOCS,
    settings.GUEST_MAX_FILE_SIZE_MB,
    settings.GUEST_MAX_PAGES,
    settings.GUEST_MAX_EXTRACTED_CHARS,
    settings.GUEST_UPLOAD_LIMIT,
    settings.GUEST_UPLOAD_WINDOW_SECONDS,
    settings.GUEST_QUERY_LIMIT,
    settings.GUEST_QUERY_WINDOW_SECONDS,
) < 1:
    raise ValueError("Guest limits and lifetimes must be positive")
if settings.SMTP_USE_SSL and settings.SMTP_STARTTLS:
    raise ValueError("Use either SMTP_USE_SSL or SMTP_STARTTLS, not both")
if urlparse(settings.FRONTEND_URL).scheme not in {"http", "https"}:
    raise ValueError("FRONTEND_URL must use http or https")
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.CHROMA_PERSIST_DIR, exist_ok=True)
