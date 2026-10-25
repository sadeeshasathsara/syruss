"""Destination kinds. Importing this package registers every adapter."""
from app.services.destinations.base import ADAPTERS, Adapter, Field, SendContext, SendResult, Target, TestResult  # noqa: F401
from app.services.destinations import webhook, rest, s3, email  # noqa: F401  (registers the adapters)
from app.services.destinations import quickbooks, xero, netsuite, sap, dynamics, odoo  # noqa: F401
