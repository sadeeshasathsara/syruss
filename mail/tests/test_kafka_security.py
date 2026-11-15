"""The Kafka login: none by default, TLS and SASL when switched on."""
import ssl

from mail.config import settings
from mail.kafka_security import kafka_security


def test_no_login_by_default():
    assert kafka_security() == {}


def test_sasl_over_tls(monkeypatch):
    monkeypatch.setattr(settings, "KAFKA_SECURITY_PROTOCOL", "sasl_ssl")
    monkeypatch.setattr(settings, "KAFKA_SASL_USERNAME", "mail")
    monkeypatch.setattr(settings, "KAFKA_SASL_PASSWORD", "a-long-password")
    got = kafka_security()
    assert got["security_protocol"] == "SASL_SSL" and got["sasl_mechanism"] == "SCRAM-SHA-512"
    assert got["sasl_plain_username"] == "mail" and isinstance(got["ssl_context"], ssl.SSLContext)


def test_tls_alone_has_no_login(monkeypatch):
    monkeypatch.setattr(settings, "KAFKA_SECURITY_PROTOCOL", "SSL")
    got = kafka_security()
    assert "sasl_mechanism" not in got and got["security_protocol"] == "SSL"
