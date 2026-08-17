from pydantic import field_validator
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
    twilio_api_key_sid: str = ""
    twilio_api_key_secret: str = ""
    twilio_phone_number: str = ""
    cordia_phone_number: str = ""
    cordia_test_phone_number: str = ""

    # Public URL where compliance pages (/consent, /privacy, /terms) are served.
    # Branded custom domain (CNAME -> Railway); the *.up.railway.app URL keeps
    # serving the same pages. Override with PUBLIC_BASE_URL env if needed.
    public_base_url: str = "https://cordia.aigenpartners.com"

    duffel_access_token: str = ""
    duffel_webhook_secret: str = ""  # from the Duffel dashboard webhook config
    enable_flight_booking: bool = False  # Duffel Links hosted checkout (off until tested)

    # Email (independent of Crown Bakeries — dedicated assistant identity)
    enable_email: bool = True
    email_provider: str = "gmail"  # "gmail" (free, SMTP+IMAP) or "resend"
    email_from_name: str = "Cordia"
    # Gmail provider (free): a dedicated Gmail + an App Password (requires 2FA)
    email_address: str = ""  # e.g. cordiaassistant@gmail.com — also the IMAP inbox
    email_app_password: str = ""
    email_poll_interval_seconds: int = 120  # how often to check the inbox for replies
    # Resend provider (optional upgrade to a branded domain)
    email_api_key: str = ""
    email_from: str = ""  # overrides the derived "Name <address>" if set
    email_inbound_secret: str = ""  # optional shared secret for the inbound webhook
    owner_email: str = ""  # Cordia's destination inbox

    enable_flight_search: bool = True
    enable_lease_review: bool = True
    enable_family_coordination: bool = True
    # Outbound drafting/approval engine (contacts + draft/send tools).
    # Off until the approval flow is verified end-to-end in staging.
    enable_outbound: bool = False

    # Naples house inbox — a second monitored Gmail (IMAP) for the property.
    # Cord summarizes inbound mail to Cordia and drafts replies for approval.
    naples_email_address: str = ""
    naples_email_app_password: str = ""
    naples_poll_interval_seconds: int = 300

    flight_monitor_interval_minutes: int = 60
    morning_brief_hour: int = 7  # local server hour for the daily brief
    birthday_prep_lead_days: int = 14  # how far ahead to proactively prep for birthdays

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("database_url", mode="before")
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        # Railway (and some other hosts) provide postgresql:// — asyncpg needs postgresql+asyncpg://
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v


settings = Settings()
