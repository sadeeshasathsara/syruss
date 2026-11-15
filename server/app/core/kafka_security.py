"""
How this service signs in to Kafka. The default is no login (a broker on a private network, as in development). For a real
deployment turn on TLS and SASL (see mail/README.md, "Kafka security"): the same four settings work for every client here.

    KAFKA_SECURITY_PROTOCOL   PLAINTEXT (default) | SSL | SASL_PLAINTEXT | SASL_SSL
    KAFKA_SASL_MECHANISM      SCRAM-SHA-512 (default) | SCRAM-SHA-256 | PLAIN
    KAFKA_SASL_USERNAME / KAFKA_SASL_PASSWORD
    KAFKA_SSL_CA_FILE         the CA that signed the broker's certificate (empty: the system's)
"""
import ssl
from typing import Any, Dict

from app.core.config import settings


def kafka_security() -> Dict[str, Any]:
    """Keyword arguments for every aiokafka client: nothing for PLAINTEXT, else the protocol, TLS context and login."""
    protocol = settings.KAFKA_SECURITY_PROTOCOL.upper()
    if protocol == "PLAINTEXT":
        return {}
    out: Dict[str, Any] = {"security_protocol": protocol}
    if protocol in ("SSL", "SASL_SSL"):
        out["ssl_context"] = ssl.create_default_context(cafile=settings.KAFKA_SSL_CA_FILE or None)
    if protocol.startswith("SASL"):
        out.update(sasl_mechanism=settings.KAFKA_SASL_MECHANISM.upper(), sasl_plain_username=settings.KAFKA_SASL_USERNAME,
                   sasl_plain_password=settings.KAFKA_SASL_PASSWORD)
    return out
