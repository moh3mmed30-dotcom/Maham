from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    app_secret: str = "CHANGE_ME"
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    bot_token: str = ""
    bot_username: str = ""
    app_base_url: str = "http://localhost:8000"
    admin_telegram_ids: str = "7391716280"
    init_data_max_age_seconds: int = 86400
    cors_origins: str = "*"
    payment_provider: str = "disabled"
    payment_webhook_secret: str = ""

    @property
    def admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_telegram_ids.split(",") if x.strip()}

settings = Settings()
