import json
from typing import Dict, Optional
from langchain_core.language_models import BaseChatModel
from agentic_framework.config import settings


def get_chat_model(purpose: str = "work") -> BaseChatModel:
    """
    The tool-calling model the agent reasons with. "work" (documents, templates) needs a strong
    multimodal model; "chat" can use CHAT_MODEL, a cheaper one from the same provider; "analytics" (looking for what is
    worth showing in the records) is the same kind of model run warmer, so it comes up with new ideas.
    """
    provider = settings.LLM_PROVIDER.lower()
    name = model_name(purpose)
    temperature = settings.ANALYTICS_TEMPERATURE if purpose == "analytics" else settings.LLM_TEMPERATURE
    if provider in ("gemini", "google"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        api_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set for the agent service.")
        return ChatGoogleGenerativeAI(
            model=name, google_api_key=api_key,
            temperature=temperature, max_retries=3,
        )
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set for the agent service.")
        return ChatAnthropic(
            model=name, api_key=settings.ANTHROPIC_API_KEY,
            temperature=temperature, max_retries=3, max_tokens=8192,
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set for the agent service.")
        return ChatOpenAI(
            model=name, api_key=settings.OPENAI_API_KEY,
            temperature=temperature, max_retries=3,
        )
    raise RuntimeError(f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'. Use gemini, anthropic or openai.")


def model_name(purpose: str = "work") -> str:
    if purpose == "analyst":
        purpose = "analytics"
    if purpose == "analytics" and settings.ANALYTICS_MODEL:
        return settings.ANALYTICS_MODEL
    if purpose in ("chat", "analytics") and settings.CHAT_MODEL:
        return settings.CHAT_MODEL
    provider = settings.LLM_PROVIDER.lower()
    return {
        "gemini": settings.GEMINI_MODEL, "google": settings.GEMINI_MODEL,
        "anthropic": settings.ANTHROPIC_MODEL, "openai": settings.OPENAI_MODEL,
    }.get(provider, provider)


def run_cost(model: str, usage: Dict[str, int]) -> Optional[float]:
    """USD for a run's tokens at the configured price; None when the model has no price set."""
    try:
        prices = json.loads(settings.MODEL_PRICES or "{}")
    except ValueError:
        return None
    price = prices.get(model)
    if not price or len(price) != 2:
        return None
    return round((usage.get("input_tokens", 0) * price[0] + usage.get("output_tokens", 0) * price[1]) / 1_000_000, 6)
