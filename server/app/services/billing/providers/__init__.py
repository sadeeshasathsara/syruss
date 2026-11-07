"""Payment providers. Importing this package registers every adapter."""
from app.services.billing.providers.base import PROVIDERS, Buyer, Provider, Purchase, provider, register  # noqa: F401
from app.services.billing.providers import manual  # noqa: F401  (registers the adapter)
