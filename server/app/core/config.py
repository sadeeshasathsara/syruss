from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Adaptive AI Document Intelligence Platform"
    API_V1_PREFIX: str = "/api/v1"
    AGENT_API_PREFIX: str = "/internal/agent/v1"

    # PostgreSQL Configuration (Docker)
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgrespassword"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "doc_intelligence"

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # MinIO Configuration (Docker)
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ROOT_USER: str = "minioadmin"
    MINIO_ROOT_PASSWORD: str = "minioadminpassword"
    MINIO_SECURE: bool = False
    MINIO_BUCKET_NAME: str = "documents"

    # "development" or "production". Production refuses the mock sign-in and the destructive dev routes.
    ENV: str = "development"
    # Keycloak (authentication only: tokens carry who you are, never what you may do)
    OIDC_ISSUER: str = "http://localhost:8080/realms/syruss"
    # Empty: <issuer>/protocol/openid-connect/certs
    OIDC_JWKS_URL: str = ""
    OIDC_AUDIENCE: str = "syruss-api"
    # Clients allowed to call the API (the token's authorized party)
    OIDC_ALLOWED_CLIENTS: str = "syruss-site,syruss-app"
    OIDC_CLOCK_SKEW_SECONDS: int = 30

    # People who run the platform itself (Keycloak subjects, comma-separated): platform-wide lessons, admin tools
    PLATFORM_ADMIN_USER_IDS: str = ""

    # Single-use tickets that let a browser open a WebSocket without a token in the URL
    WS_TICKET_TTL_SECONDS: int = 30
    INVITE_DAYS: int = 7
    # Invitations are for an email address; accepting one proves you own it. Turn off only in development,
    # where Keycloak has no mail server to verify addresses.
    INVITE_REQUIRE_VERIFIED_EMAIL: bool = True

    # Where people open the links in our emails (the public site: invitations land at <site>/invite/<token>)
    PUBLIC_SITE_URL: str = "http://localhost:3100"

    # Email. "smtp" sends through SMTP_*; "console" only logs the message (local work without a mail server);
    # "memory" keeps messages in a list (tests). Locally, Mailpit (bridge/docker-compose.yml) catches everything on 1025.
    MAIL_BACKEND: str = "smtp"
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_SECURITY: str = "none"  # none | starttls | ssl
    SMTP_TIMEOUT_SECONDS: int = 15
    MAIL_FROM: str = "no-reply@syruss.local"
    MAIL_FROM_NAME: str = "Syruss"
    MAIL_REPLY_TO: str = ""
    # An invitation email can be sent again this often, no sooner
    MAIL_RESEND_SECONDS: int = 60

    # A browser the account hasn't used before must be confirmed with an emailed code, then is trusted for a while.
    REQUIRE_TRUSTED_DEVICE: bool = True
    DEVICE_TRUST_DAYS: int = 30        # how long a remembered browser stays trusted (renewed while it is used)
    DEVICE_SESSION_HOURS: int = 12     # for a browser the person chose not to remember
    DEVICE_MAX_PER_USER: int = 25      # more than this and the oldest unused ones are dropped

    # Confirming an email address with a one-time code before an account exists (people signing in with Google skip it)
    REQUIRE_EMAIL_VERIFICATION: bool = True
    OTP_LENGTH: int = 6
    OTP_TTL_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    OTP_RESEND_SECONDS: int = 30
    OTP_SENDS_PER_HOUR: int = 6

    # The biggest request body the server reads at all (workspaces set their own, smaller, limits for uploads)
    MAX_REQUEST_MB: int = 60

    # Risky actions (API keys, webhooks, closing things, changing who owns what) need a sign-in from the last few minutes
    REQUIRE_RECENT_LOGIN: bool = True
    RECENT_LOGIN_MINUTES: int = 15

    # Browser origins allowed to call the API directly (comma-separated). Never "*": requests carry credentials.
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:3100"

    @property
    def cors_origins(self) -> list:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip() and o.strip() != "*"]

    @property
    def oidc_allowed_clients(self) -> list:
        return [c.strip() for c in self.OIDC_ALLOWED_CLIENTS.split(",") if c.strip()]

    @property
    def platform_admin_ids(self) -> set:
        return {u.strip() for u in self.PLATFORM_ADMIN_USER_IDS.split(",") if u.strip()}

    @property
    def is_production(self) -> bool:
        return self.ENV.lower() == "production"

    # Kafka (agent events out, agent telemetry in)
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    AGENT_EVENTS_TOPIC: str = "agent.events"
    AGENT_TELEMETRY_TOPIC: str = "agent.telemetry"

    # Signs the workspace-scoped tokens attached to agent events. Only the core server
    # knows it; the agent service just forwards the token it received with each event.
    AGENT_TOKEN_SECRET: str = "dev-only-change-me"
    # Production: an Ed25519 keypair in PEM. The core signs with the private key; the agent service gets only the
    # public key (AGENT_TOKEN_PUBLIC_KEY in its own env), so it can check tokens but never make them.
    AGENT_TOKEN_PRIVATE_KEY: str = ""
    AGENT_TOKEN_PUBLIC_KEY: str = ""
    # Long enough to cover an agent backlog; a token only ever grants one workspace, for one event.
    AGENT_TOKEN_TTL_SECONDS: int = 86400

    # Apply database migrations when the API starts. Production deploys run `alembic upgrade head`
    # as their own step and set this to false.
    AUTO_MIGRATE: bool = True

    # Memory embeddings (made by the agent service). Must match the agent's EMBEDDING_DIM.
    EMBEDDING_DIM: int = 768

    # The platform's recurring work per workspace: reflection, memory consolidation, graph upkeep
    # (app/services/scheduler.py). Safe to run in several server processes.
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_INTERVAL_SECONDS: int = 60

    # Connections: how other systems send documents in and receive approved data out.
    PUBLIC_API_URL: str = "http://localhost:8000"          # what senders are told to call (shown in the Connections pages)
    MINIO_PUBLIC_ENDPOINT: str = ""                        # host:port outside senders use for presigned uploads (default: MINIO_ENDPOINT)
    MINIO_PUBLIC_SECURE: bool = False
    TRUST_PROXY_HEADERS: bool = False                      # read the sender's address from X-Forwarded-For (only behind a proxy you control)
    INTAKE_MAX_PIXELS: int = 100_000_000                   # an image larger than this is refused (decompression bombs)
    INTAKE_UPLOAD_MINUTES: int = 15                        # how long a presigned upload link works
    CLAMAV_HOST: str = ""                                  # when set, every intake file is scanned by clamd before it is stored
    CLAMAV_PORT: int = 3310
    INTAKE_SMTP_PORT: int = 0                              # 0 = no built-in email receiver
    INTAKE_SMTP_HOST: str = "0.0.0.0"
    INTAKE_EMAIL_DOMAIN: str = "intake.syruss.local"       # the domain whose MX points at the receiver
    INTAKE_SFTP_PORT: int = 0                              # 0 = no built-in SFTP server
    INTAKE_SFTP_HOST: str = "0.0.0.0"
    INTAKE_SFTP_HOST_KEY: str = ""                         # path of the server's host key (created on first start when missing)
    INTAKE_SFTP_PUBLIC_HOST: str = ""                      # the name senders connect to (shown in the app); empty: the host of PUBLIC_API_URL
    DELIVERY_WORKER_ENABLED: bool = True
    DELIVERY_POLL_SECONDS: float = 2.0
    DELIVERY_TIMEOUT_SECONDS: int = 20
    DELIVERY_MAX_ATTEMPTS: int = 8
    DELIVERY_BREAKER_FAILURES: int = 10
    OAUTH_REDIRECT_BASE: str = ""                          # public URL of this API for OAuth callbacks (default: PUBLIC_API_URL)

    # Credentials of knowledge sources (API keys, passwords) are encrypted with this Fernet key.
    # Empty in development: a key is derived from AGENT_TOKEN_SECRET. Set a real key in production.
    SECRETS_ENCRYPTION_KEY: str = ""
    # Knowledge sources the server fetches on the workspace's behalf.
    KNOWLEDGE_SOURCE_MAX_BYTES: int = 5_000_000
    KNOWLEDGE_SOURCE_TIMEOUT_SECONDS: float = 20.0
    # Fetching from private, loopback or link-local addresses is refused (server-side request
    # forgery); allow it only for local development against an API on your own machine.
    KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS: bool = False

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
