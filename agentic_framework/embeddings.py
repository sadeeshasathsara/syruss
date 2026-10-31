"""
Embeddings for memory: turning text into vectors the core server ranks by meaning (pgvector).

Optional by design. With no embedding model for the provider, no key, or a failed call, these
return None and the core server falls back to text search; nothing else changes.
"""
import logging
from functools import lru_cache
from typing import List, Optional
from agentic_framework.config import settings

logger = logging.getLogger("agent.embeddings")

DEFAULT_MODELS = {"gemini": "models/gemini-embedding-001", "google": "models/gemini-embedding-001", "openai": "text-embedding-3-small"}


@lru_cache(maxsize=1)
def _model():
    if not settings.EMBEDDINGS_ENABLED:
        return None
    provider = settings.LLM_PROVIDER.lower()
    name = settings.EMBEDDING_MODEL or DEFAULT_MODELS.get(provider)
    try:
        if provider in ("gemini", "google"):
            api_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
            if not api_key or not name:
                return None
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
            return ("gemini", GoogleGenerativeAIEmbeddings(model=name, google_api_key=api_key))
        if provider == "openai":
            if not settings.OPENAI_API_KEY or not name:
                return None
            from langchain_openai import OpenAIEmbeddings
            return ("openai", OpenAIEmbeddings(model=name, api_key=settings.OPENAI_API_KEY, dimensions=settings.EMBEDDING_DIM))
    except Exception as exc:  # a missing package or bad config switches embeddings off, not the agent
        logger.warning("Embeddings unavailable (%s); memory falls back to text search.", exc)
    return None


def _valid(vector: Optional[List[float]]) -> Optional[List[float]]:
    return vector if vector and len(vector) == settings.EMBEDDING_DIM else None


async def embed_query(text: str) -> Optional[List[float]]:
    """A vector for something being looked up."""
    model = _model()
    if not model or not text.strip():
        return None
    kind, m = model
    try:
        if kind == "gemini":
            return _valid(await m.aembed_query(text, task_type="RETRIEVAL_QUERY", output_dimensionality=settings.EMBEDDING_DIM))
        return _valid(await m.aembed_query(text))
    except Exception as exc:
        logger.warning("Embedding a query failed (%s); using text search.", exc)
        return None


async def embed_documents(texts: List[str]) -> List[Optional[List[float]]]:
    """Vectors for things being remembered (one per text, None where it failed)."""
    model = _model()
    if not model or not texts:
        return [None] * len(texts)
    kind, m = model
    try:
        if kind == "gemini":
            vectors = await m.aembed_documents(texts, task_type="RETRIEVAL_DOCUMENT", output_dimensionality=settings.EMBEDDING_DIM)
        else:
            vectors = await m.aembed_documents(texts)
        return [_valid(v) for v in vectors]
    except Exception as exc:
        logger.warning("Embedding %d memories failed (%s); they stay text-only for now.", len(texts), exc)
        return [None] * len(texts)
