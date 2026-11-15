from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class MailSettings(BaseSettings):
    """
    The mail service: the one place Syruss's mail credentials live and the only thing that talks to mail providers.
    The server publishes what to send on Kafka (mail.outbound); this service sends it through the account routed to
    that purpose and reports how it went (mail.events).
    """

    # "development" (your machine), "test" (the test server) or "production". The test server and production refuse to
    # start with the development values below (see check_config).
    ENV: str = "development"

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    MAIL_OUTBOUND_TOPIC: str = "mail.outbound"   # jobs to send, from the server
    MAIL_EVENTS_TOPIC: str = "mail.events"       # what happened to each job, back to the server
    CONSUMER_GROUP: str = "mail"

    # Its own database (a separate database from the core server's, on the same Postgres)
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgrespassword"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    MAIL_DB: str = "syruss_mail"

    @property
    def DATABASE_URL(self) -> str:
        return (f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.MAIL_DB}")

    # Seals the credentials of connected accounts (a Fernet key: python -c "from cryptography.fernet import Fernet;
    # print(Fernet.generate_key().decode())"). Development derives one from a fixed phrase.
    MAIL_SECRETS_KEY: str = ""

    # The account from the environment: the fallback every purpose uses until an account connected in the admin app
    # takes over (a fresh install sends sign-in codes and invitations before anyone can connect one).
    # "smtp" sends through MAIL_SMTP_*; "console" only logs (local work without a mail server); "memory" keeps them (tests).
    MAIL_BACKEND: str = "smtp"
    MAIL_SMTP_HOST: str = "localhost"
    MAIL_SMTP_PORT: int = 1025
    MAIL_SMTP_USERNAME: str = ""
    MAIL_SMTP_PASSWORD: str = ""
    MAIL_SMTP_SECURITY: str = "none"  # none | starttls | ssl
    MAIL_SMTP_TIMEOUT_SECONDS: int = 15
    MAIL_FROM: str = "no-reply@syruss.local"
    MAIL_FROM_NAME: str = "Syruss"

    # The account from the environment can also read a mailbox (IMAP, read-only), for purposes that read (support) until
    # an account connected in the admin app takes over. Empty host: it doesn't read.
    MAIL_IMAP_HOST: str = ""
    MAIL_IMAP_PORT: int = 993
    MAIL_IMAP_USERNAME: str = ""
    MAIL_IMAP_PASSWORD: str = ""
    MAIL_IMAP_FOLDER: str = "INBOX"
    MAIL_IMAP_SSL: bool = True
    MAIL_POLL_SECONDS: int = 60        # how often mailboxes are read
    MAIL_INBOUND_TOPIC: str = "mail.inbound"
    INBOUND_KEEP_DAYS: int = 7         # mail read but never collected by the server is dropped after this

    # The internal API the server uses (api.py): accounts, routes, the delivery log, connecting Google, collecting
    # mail read from mailboxes. Only the server calls it, with MAIL_SERVICE_TOKEN (the same value in the server's env).
    MAIL_API_HOST: str = "127.0.0.1"
    MAIL_API_PORT: int = 8100
    MAIL_SERVICE_TOKEN: str = ""

    # Google mail (accounts/gmail.py): an OAuth client of type "Web application" in Google Cloud, with the admin app's
    # <PUBLIC_ADMIN_URL>/email/google as its redirect URI. Empty: Google can't be connected.
    GOOGLE_MAIL_CLIENT_ID: str = ""
    GOOGLE_MAIL_CLIENT_SECRET: str = ""

    # The SMTP door for Keycloak (relay.py): Keycloak sends its emails here with this login, From MAIL_FROM, and they
    # leave through the "accounts" purpose's account. Reachable only on the internal network (deploy/*: the mail-relay
    # network; on your machine 127.0.0.1, and Keycloak's container reaches it as host.docker.internal). 0: switched off.
    MAIL_RELAY_HOST: str = "127.0.0.1"
    MAIL_RELAY_PORT: int = 2525
    MAIL_RELAY_USERNAME: str = "keycloak"
    MAIL_RELAY_PASSWORD: str = ""

    # Delivery: a job that fails is tried again later, waiting longer each time, until it is given up
    MAX_ATTEMPTS: int = 6
    RETRY_BASE_SECONDS: int = 30      # then 60, 120, ... (doubling), capped at RETRY_MAX_SECONDS
    RETRY_MAX_SECONDS: int = 3600
    RETRY_POLL_SECONDS: int = 10

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_deployed(self) -> bool:
        return self.ENV.lower() in ("test", "production")


ENVIRONMENTS = ("development", "test", "production")


def deployment_problems(s: MailSettings) -> list:
    """Development values the test server and production refuse, in plain words (empty means fine)."""
    problems = []
    if s.POSTGRES_PASSWORD == "postgrespassword":
        problems.append("POSTGRES_PASSWORD still has its development value")
    if not s.MAIL_SECRETS_KEY:
        problems.append("MAIL_SECRETS_KEY is not set")
    if s.MAIL_BACKEND.lower() != "smtp":
        problems.append("MAIL_BACKEND must be smtp")
    if s.MAIL_SMTP_HOST in ("localhost", "127.0.0.1"):
        problems.append("MAIL_SMTP_HOST must point at a real mail server")
    if s.MAIL_FROM.endswith((".local", ".localhost", ".test")):
        problems.append("MAIL_FROM must be an address at a real domain")
    if len(s.MAIL_SERVICE_TOKEN) < 24:
        problems.append("MAIL_SERVICE_TOKEN must be a long random value (the server's key to this service)")
    if s.MAIL_RELAY_PORT and len(s.MAIL_RELAY_PASSWORD) < 16:
        problems.append("MAIL_RELAY_PASSWORD must be a long random value (Keycloak's login to the mail relay)")
    return problems


def check_config(s: MailSettings) -> None:
    """Fails startup on an unknown ENV or backend, or a real server still running with development values."""
    if s.ENV.lower() not in ENVIRONMENTS:
        raise RuntimeError(f"ENV must be one of {', '.join(ENVIRONMENTS)} (it is '{s.ENV}').")
    if s.MAIL_BACKEND.lower() not in ("smtp", "console", "memory"):
        raise RuntimeError("MAIL_BACKEND must be smtp, console or memory.")
    if s.MAIL_SMTP_SECURITY.lower() not in ("none", "starttls", "ssl"):
        raise RuntimeError("MAIL_SMTP_SECURITY must be none, starttls or ssl.")
    problems = deployment_problems(s) if s.is_deployed else []
    if problems:
        raise RuntimeError(f"Not safe to run as {s.ENV.lower()}: " + "; ".join(problems))


settings = MailSettings()
