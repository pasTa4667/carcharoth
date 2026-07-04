"""Secrets and environment-specific settings, loaded from .env / environment."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    apca_api_key_id: str
    apca_api_secret_key: str
    apca_api_base_url: str = "https://paper-api.alpaca.markets/v2"
    database_url: str = "postgresql+psycopg://carcharoth:carcharoth@localhost:5432/carcharoth"
