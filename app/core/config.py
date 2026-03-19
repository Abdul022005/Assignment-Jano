from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongodb_uri: str = "mongodb://admin:secret@localhost:27017/med_reconciliation?authSource=admin"
    mongodb_db_name: str = "med_reconciliation"
    app_env: str = "development"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()