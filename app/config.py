from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Cordia"
    debug: bool = False
    app_port: int = 8000

    database_url: str = "postgresql+asyncpg://cordia:password@localhost:5432/cordia_db"

    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    cordia_phone_number: str = ""

    amadeus_client_id: str = ""
    amadeus_client_secret: str = ""
    amadeus_environment: str = "test"

    enable_flight_search: bool = True
    enable_lease_review: bool = True
    enable_family_coordination: bool = True

    flight_monitor_interval_minutes: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
