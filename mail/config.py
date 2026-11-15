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
    # Kafka login (kafka_security.py): off by default. TLS and SASL for any broker that is not on a private network.
    KAFKA_SECURITY_PROTOCOL: str = "PLAINTEXT"   # PLAINTEXT | SSL | SASL_PLAINTEXT | SASL_SSL
    KAFKA_SASL_MECHANISM: str = "SCRAM-SHA-512"
    KAFKA_SASL_USERNAME: str = ""
    KAFKA_SASL_PASSWORD: str = ""
    KAFKA_SSL_CA_FILE: str = ""
    MAIL_OUTBOUND_TOPIC: str = "mail.outbound"   # jobs to send, from the server
    MAIL_EVENTS_TOPIC: str = "mail.events"       # what happened to each job, back to the server
    # The control plane (the server's signed commands and this service's answers) and the public state it keeps the
    # server told of. Created at start-up with the settings each needs (bus.py).
    MAIL_COMMANDS_TOPIC: str = "mail.commands"
    MAIL_REPLIES_TOPIC: str = "mail.replies"
    MAIL_DIRECTORY_TOPIC: str = "mail.directory"       # compacted: the latest state of each job and account
    MAIL_INBOUND_ACKS_TOPIC: str = "mail.inbound.acks"  # the server saying it took a message in
    MAIL_MAX_MESSAGE_BYTES: int = 25_000_000            # the largest email carried on Kafka
    MAIL_TOPIC_REPLICAS: int = 1                        # one broker: 1. Raise it with the cluster.
    DIRECTORY_REFRESH_SECONDS: int = 60                 # how often the directory is published again, whatever changed
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

    # Seals what is kept in the database: Fernet keys, comma-separated, NEWEST FIRST (python -m mail keygen). Add a key at
    # the front to rotate: every older one still opens what it sealed. Development derives one from a fixed phrase.
    MAIL_MASTER_KEYS: str = ""
    MAIL_SECRETS_KEY: str = ""   # the single key before MAIL_MASTER_KEYS: still honoured, and still opens old data

    # The server's public signing key (base64, 32 bytes). Only commands signed with the matching private key are carried
    # out. Empty: no command is accepted.
    MAIL_TRUSTED_SERVER_KEY: str = ""
    COMMAND_MAX_AGE_SECONDS: int = 300

    # What a job sends through before a mailbox is connected: "none" (the default: mail waits until one is connected in the admin
    # app or with `python -m mail connect`; no mail credential is ever kept in an environment file), "console" (only logs the
    # message: local work without a mail server) or "memory" (keeps messages: tests).
    MAIL_BACKEND: str = "none"
    MAIL_SMTP_TIMEOUT_SECONDS: int = 15
    MAIL_FROM: str = "no-reply@syruss.local"
    MAIL_FROM_NAME: str = "Syruss"
    # What mail says about itself, for the server and for every message that doesn't say otherwise (the server asks for
    # these at GET /identity and puts none of them in its own environment). MAIL_REPLY_TO: where replies to app mail go
    # (empty: to the From address). SUPPORT_EMAIL_ADDRESS: the support mailbox as people write to it (empty: MAIL_FROM);
    # support mail is sent in the name SUPPORT_FROM_NAME and answered at that address.
    MAIL_REPLY_TO: str = ""
    SUPPORT_EMAIL_ADDRESS: str = ""
    SUPPORT_FROM_NAME: str = "Syruss Support"

    MAIL_POLL_SECONDS: int = 60        # how often mailboxes are read
    MAIL_INBOUND_TOPIC: str = "mail.inbound"
    INBOUND_KEEP_DAYS: int = 7         # mail read but never collected by the server is dropped after this

    # Google mail (accounts/gmail.py): an OAuth client of type "Web application" in Google Cloud, with the admin app's
    # <PUBLIC_ADMIN_URL>/email/google as its redirect URI. Empty: Google can't be connected.
    GOOGLE_MAIL_CLIENT_ID: str = ""
    GOOGLE_MAIL_CLIENT_SECRET: str = ""

    # Microsoft mail (accounts/graph.py): an app registration in Microsoft Entra, with a Web redirect URI of
    # <PUBLIC_ADMIN_URL>/email/microsoft, the delegated Graph permissions Mail.Send and Mail.Read, and a client secret (its Value).
    # MICROSOFT_MAIL_TENANT: common (work and personal accounts, the default), organizations, consumers, or a directory id or domain.
    # Empty id or secret: Microsoft can only be set in the admin app (Email, Provider apps), where it takes precedence.
    MICROSOFT_MAIL_CLIENT_ID: str = ""
    MICROSOFT_MAIL_CLIENT_SECRET: str = ""
    MICROSOFT_MAIL_TENANT: str = ""

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
    if not (s.MAIL_MASTER_KEYS or s.MAIL_SECRETS_KEY):
        problems.append("MAIL_MASTER_KEYS (or MAIL_SECRETS_KEY) is not set")
    if s.MAIL_BACKEND.lower() != "none":
        problems.append("MAIL_BACKEND must be none (mailboxes are connected in the admin app, not logged or kept in memory)")
    if s.MAIL_FROM.endswith((".local", ".localhost", ".test")):
        problems.append("MAIL_FROM must be an address at a real domain")
    if not s.MAIL_TRUSTED_SERVER_KEY:
        problems.append("MAIL_TRUSTED_SERVER_KEY is not set (the public half of the server's signing key: python -m app.mail_signing_key)")
    if s.MAIL_RELAY_PORT and len(s.MAIL_RELAY_PASSWORD) < 16:
        problems.append("MAIL_RELAY_PASSWORD must be a long random value (Keycloak's login to the mail relay)")
    return problems


def check_config(s: MailSettings) -> None:
    """Fails startup on an unknown ENV or backend, or a real server still running with development values."""
    if s.ENV.lower() not in ENVIRONMENTS:
        raise RuntimeError(f"ENV must be one of {', '.join(ENVIRONMENTS)} (it is '{s.ENV}').")
    if s.MAIL_BACKEND.lower() not in ("none", "smtp", "console", "memory"):   # "smtp": the old value; it is read as none
        raise RuntimeError("MAIL_BACKEND must be none, console or memory (smtp is read as none).")
    problems = deployment_problems(s) if s.is_deployed else []
    if problems:
        raise RuntimeError(f"Not safe to run as {s.ENV.lower()}: " + "; ".join(problems))


settings = MailSettings()
