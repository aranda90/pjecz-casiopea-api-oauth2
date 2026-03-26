"""
Settings
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()
SERVICE_PREFIX = os.getenv("SERVICE_PREFIX", "pjecz_casiopea_api_oauth2")


class Settings(BaseSettings):
    """Settings"""

    ACCESS_TOKEN_EXPIRE_SECONDS: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_SECONDS", "3600"))
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    CONTROL_ACCESO_URL: str = os.getenv("CONTROL_ACCESO_URL", "")
    CONTROL_ACCESO_API_KEY: str = os.getenv("CONTROL_ACCESO_API_KEY", "")
    CONTROL_ACCESO_APLICACION: int = int(os.getenv("CONTROL_ACCESO_APLICACION", "0"))
    CONTROL_ACCESO_TIMEOUT: int = int(os.getenv("CONTROL_ACCESO_TIMEOUT", "60"))
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_NAME: str = os.getenv("DB_NAME", "pjecz_casiopea")
    DB_PASS: str = os.getenv("DB_PASS", "")
    DB_USER: str = os.getenv("DB_USER", "")
    HOST: str = os.getenv("HOST", "")
    NEW_ACCOUNT_WEB_PAGE_URL: str = os.getenv("NEW_ACCOUNT_WEB_PAGE_URL", "http://localhost:3000/registros/confirmar")
    ORIGINS: str = os.getenv("ORIGINS", "http://127.0.0.1:3000,http://localhost:3000")
    RECOVER_WEB_PAGE_URL: str = os.getenv("RECOVER_WEB_PAGE_URL", "http://localhost:3000/recuperaciones/confirmar")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
    SENDGRID_FROM_EMAIL: str = os.getenv("SENDGRID_FROM_EMAIL", "")
    TASK_QUEUE: str = os.getenv("TASK_QUEUE", "pjecz_casiopea")
    TZ: str = os.getenv("TZ", "America/Mexico_City")
    DEBUG_ALLOW_TODAY: bool = False

    class Config:
        """Load configuration"""

        @classmethod
        def customise_sources(cls, init_settings, env_settings, file_secret_settings):
            """Change the order of precedence of settings sources"""
            return env_settings, file_secret_settings, init_settings


@lru_cache()
def get_settings() -> Settings:
    """Get Settings"""
    return Settings()
