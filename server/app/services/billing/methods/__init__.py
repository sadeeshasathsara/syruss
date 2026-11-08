"""Ways to pay an invoice. Importing this package registers every one."""
from app.services.billing.methods.base import METHODS, Method, enabled, method, register  # noqa: F401
from app.services.billing.methods import bank_transfer, staff_recorded  # noqa: F401  (registers them)
