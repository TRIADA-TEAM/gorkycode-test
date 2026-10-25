from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/assistant.db"
    default_timezone: str = "Europe/Moscow"

    gigachat_auth_key: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-3-Lightning"
    gigachat_oauth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_api_url: str = "https://api.giga.chat/v1"
    gigachat_verify_ssl: bool = True

    polza_api_key: str = ""
    polza_api_url: str = "https://polza.ai/api/v1"
    polza_stt_model: str = "openai/whisper-large-v3-turbo"
    polza_stt_language: str = "ru"
    polza_stt_timeout: float = 60.0
    polza_stt_max_retries: int = 3

    max_audio_bytes: int = 10 * 1024 * 1024
    import_seed: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
