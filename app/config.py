from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Cordia"
    debug: bool = False
    app_port: int = 8000

    database_url: str = "postgresql+asyncpg://cordia:password@localhost:5432/cordia_db"

    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # Active SMS provider: "twilio" or "signalhouse". Everything above the
    # send/receive layer (consent, keywords, drafting) is provider-agnostic.
    sms_provider: str = "twilio"

    # Signal House (10DLC campaign + number live here)
    signalhouse_api_key: str = ""
    signalhouse_phone_number: str = ""  # e.g. +16292259067
    signalhouse_base_url: str = "https://v2.signalhouse.io"
    signalhouse_send_path: str = "/message/sms"
    signalhouse_webhook_secret: str = ""  # shared secret for /webhook/signalhouse

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
    # Display name on outgoing mail. "Cord" so a recipient sees the assistant,
    # not a message that appears to come from Cordia herself.
    email_from_name: str = "Cord"
    # Gmail provider (free): a dedicated Gmail + an App Password (requires 2FA)
    email_address: str = ""  # e.g. cordiaassistant@gmail.com — also the IMAP inbox
    email_app_password: str = ""
    email_poll_interval_seconds: int = 120  # how often to check the inbox for replies
    # Resend provider (optional upgrade to a branded domain)
    email_api_key: str = ""
    email_from: str = ""  # overrides the derived "Name <address>" if set
    email_inbound_secret: str = ""  # shared secret in the webhook URL (fallback auth)
    # Resend's webhook signing secret (whsec_...). Preferred over the URL
    # secret: it proves the request came from Resend and was not altered.
    email_webhook_signing_secret: str = ""
    owner_email: str = ""  # Cordia's destination inbox

    # The principals — Cordia and anyone she works with who gets their own full
    # assistant (her husband, her assistant). Deliberately NOT in the repo: real
    # names, mobile numbers and addresses, same reasoning as the family roster.
    # Shape: [{"name": "...", "phone": "...", "email": "...", "is_owner": true}]
    # Unset falls back to creating Cordia alone from the settings below.
    principals_json: str = ""
    seed_principals_on_startup: bool = True

    # The family roster itself, as JSON. Deliberately NOT in the repo: it holds
    # names, children's dates of birth, phone numbers and home addresses.
    family_seed_json: str = ""
    family_seed_path: str = ""  # local-dev / CLI alternative; inline wins

    # Load Cordia's family roster on boot (idempotent). Keeps a fresh or reset
    # database self-healing instead of depending on someone running a script.
    seed_family_on_startup: bool = True

    # Gates the admin/data APIs (/api/v1/*, /health/config, /health/data).
    # Deliberately separate from signalhouse_webhook_secret, which is shared
    # with the SMS vendor. Unset means those routes are denied, not open.
    admin_api_secret: str = ""

    # Password for the human status page at /health/dashboard. Unset means the
    # page cannot be logged into at all — it fails closed like the admin API.
    dashboard_password: str = ""
    dashboard_session_hours: int = 12

    # Fernet key encrypting loyalty account numbers at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Unset means loyalty numbers cannot be stored at all (fails closed).
    loyalty_encryption_key: str = ""

    # Cost tracking rates, USD. Defaults are typical US list prices; set them to
    # the real contracted rates so the dashboard reports actual spend rather than
    # an estimate. The dashboard states which rates it used.
    # Signal House bills a platform fee plus a carrier passthrough, and the two
    # directions are not symmetric — inbound carries no platform fee at all.
    #   SMS out  = 0.0065 platform + 0.0040 carrier   (per SEGMENT)
    #   SMS in   = 0      platform + 0.0040 carrier   (per SEGMENT)
    #   MMS      = 0.02   platform + 0.01   carrier   (per MESSAGE, either way)
    # Carrier passthrough varies a little by network — outbound 0.0100 (AT&T,
    # Google Voice) to 0.0115 (US Cellular), inbound 0 (Verizon) to 0.0045
    # (interop) — so these are the default-carrier figures.
    # Reconciled against the first invoice (278 outbound segments, 45 inbound,
    # $3.167 — matches to the penny). This account's traffic is T-Mobile and
    # Verizon, not the generic default carrier.
    sms_cost_outbound: float = 0.0110    # 0.0065 platform + 0.0045 carrier
    sms_cost_inbound: float = 0.0025     # carrier only — no platform fee inbound
    mms_cost_outbound: float = 0.03      # per message — MMS has no segments
    mms_cost_inbound: float = 0.03
    email_cost_outbound: float = 0.0004  # Resend $20/mo ÷ 50k emails
    email_cost_inbound: float = 0.0
    web_search_cost: float = 0.01        # Anthropic: $10 per 1,000 searches
    web_fetch_cost: float = 0.0          # billed as tokens, not per fetch

    # Charges that accrue whether or not anyone sends a thing. Reporting only
    # per-message cost would understate the real monthly bill.
    monthly_number_cost: float = 1.00    # local number renewal
    # The campaign charge turned out to be one-time, not recurring: the invoice
    # lists it as "Creation Low Volume, qty 1" beside the campaign creation fee.
    # It lives in setup_cost_to_date instead.
    monthly_campaign_cost: float = 0.0
    # One-time 10DLC setup already paid, from the invoice: campaign $16.50
    # (creation $15 + low-volume creation $1.50) + brand $4.50 + number purchase
    # $1.00. Shown separately so sunk cost never inflates a month's total.
    setup_cost_to_date: float = 22.00

    # Signal House runs on prepaid credit: $50 bought plus a $75 review credit.
    # Spend-to-date is on the dashboard already; the number actually worth
    # knowing is when a top-up is due.
    signalhouse_credit_purchased: float = 125.00
    # EVERYTHING Signal House had charged before this ledger existed — setup
    # ($22.00: brand, campaign, number) plus messaging ($3.19). All of it draws
    # down the same balance, so counting only messaging would read high by the
    # setup. 125.00 - 25.19 = 99.81, which matches the account.
    #
    # Re-sync this from the invoice occasionally: the monthly number renewal is
    # charged by Signal House but not recorded in this ledger, so the remaining
    # figure drifts high by about $1 a month between syncs.
    signalhouse_spend_before_ledger: float = 25.19

    # Live web research via Anthropic's server-side search/fetch tools. Costs
    # ~$0.01 per search on top of tokens, so the per-turn ceilings are config.
    enable_web_research: bool = True
    web_search_max_uses: int = 8
    web_fetch_max_uses: int = 5

    enable_flight_search: bool = True
    enable_lease_review: bool = True
    enable_family_coordination: bool = True
    # Outbound drafting/approval engine (contacts + draft/send tools). Nothing
    # sends without an approval code the principal typed herself, checked
    # against her own persisted messages — the model cannot assert it.
    enable_outbound: bool = True

    # Naples house inbox — a second monitored Gmail (IMAP) for the property.
    # Cord summarizes inbound mail to Cordia and drafts replies for approval.
    naples_email_address: str = ""
    naples_email_app_password: str = ""
    naples_poll_interval_seconds: int = 300

    # APScheduler's timezone. Without this it uses the container's local zone
    # (UTC), so the "local hour" settings below fired in the middle of the night.
    scheduler_timezone: str = "America/Chicago"

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
