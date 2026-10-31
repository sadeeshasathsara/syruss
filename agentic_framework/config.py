from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Configuration of the agent service. It has no access to the business database."""

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    AGENT_EVENTS_TOPIC: str = "agent.events"
    AGENT_TELEMETRY_TOPIC: str = "agent.telemetry"
    AGENT_DLQ_TOPIC: str = "agent.events.dlq"
    CONSUMER_GROUP: str = "workspace-agents"

    # "production" demands the real public key below. Development derives a keypair from the shared dev secret.
    ENV: str = "development"
    # Ed25519 public key (PEM) of the core server's agent-token signing key. This service can verify, never sign.
    AGENT_TOKEN_PUBLIC_KEY: str = ""
    AGENT_TOKEN_SECRET: str = "dev-only-change-me"

    # Core server (Agent Tool API)
    CORE_API_URL: str = "http://localhost:8000/internal/agent/v1"
    CORE_API_TIMEOUT_SECONDS: float = 60.0

    # The agent's own memory store (a separate database from the core server's)
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgrespassword"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    AGENT_MEMORY_DB: str = "agent_memory"

    @property
    def MEMORY_DATABASE_URL(self) -> str:
        return (f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.AGENT_MEMORY_DB}")

    # Model
    LLM_PROVIDER: str = "gemini"  # gemini | anthropic | openai
    GEMINI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-sonnet-5-5"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.1
    # The analyst (custom screens, "what stands out?") is meant to think of new things to look at, so it runs warmer than the
    # work that must be exact. Its figures still come from the platform, never from the model.
    ANALYTICS_TEMPERATURE: float = 0.7
    ANALYTICS_MODEL: str = ""        # empty: the chat model if set, else the work model

    # Budgets per wake-up (one event)
    MAX_STEPS: int = 40              # model calls
    # Chats can use a smaller, cheaper model from the same provider; empty uses the work model.
    CHAT_MODEL: str = ""

    # Memory. Embeddings let the core server rank memories by meaning (pgvector); without them it
    # searches words. EMBEDDING_DIM must match the core server's.
    EMBEDDINGS_ENABLED: bool = True
    EMBEDDING_MODEL: str = ""        # empty: the provider's default (Gemini or OpenAI)
    EMBEDDING_DIM: int = 768
    # A chat's older messages are folded into its summary once this many are outside the window.
    SUMMARY_BATCH: int = 10
    # USD per million tokens, as JSON: {"model-name": [input, output]}. Without a price the run
    # log shows tokens and leaves the cost empty (it is never guessed).
    MODEL_PRICES: str = "{}"
    MAX_RUN_SECONDS: int = 600
    MODEL_CALL_TIMEOUT_SECONDS: int = 120
    MAX_OUTCOME_REMINDERS: int = 2   # times the agent is reminded of an unfinished obligation
    MAX_IMAGES_IN_CONTEXT: int = 4   # older page views are dropped from the prompt
    FULL_TOOL_RESULTS: int = 6       # the newest tool results stay whole; older ones are cut short
    OLD_TOOL_RESULT_CHARS: int = 600

    # Delivery
    MAX_ATTEMPTS: int = 3            # per event, then dead-letter
    MAX_PARALLEL_WORKSPACES: int = 4

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = AgentSettings()
