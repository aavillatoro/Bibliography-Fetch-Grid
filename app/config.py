from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    test_database_url: str | None = None
    openalex_mailto: str | None = None
    openalex_api_key: str | None = None
    # tests turn this off and step the worker by hand
    run_worker: bool = True


settings = Settings()
