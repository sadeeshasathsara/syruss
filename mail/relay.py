"""
The SMTP door for Keycloak. Keycloak can only send through an SMTP server with a password, so the mail service is
that server: Keycloak's emails (verify an address, reset a password, a new staff member's set-up link) arrive here and
leave like any other job, through the account routed to the "accounts" purpose.

It is no open relay:
- it is reachable only on the internal network between Keycloak and the mail service (the compose files' mail-relay
  network), or on this machine in development;
- it needs Keycloak's own login (MAIL_RELAY_USERNAME and MAIL_RELAY_PASSWORD);
- it takes mail only From MAIL_FROM (the address Keycloak is set up with), to at most a few recipients, of a modest size.

There is no TLS on this hop: it never leaves the internal network. Leave MAIL_RELAY_PORT at 0 and nothing listens.
"""
import asyncio
import email
import hashlib
import hmac
import logging
import uuid
from email import policy
from email.utils import parseaddr
from typing import Awaitable, Callable, Optional
from aiosmtpd.smtp import SMTP, AuthResult, Envelope, LoginPassword, Session
from mail.accounts.base import Outbound
from mail.config import settings

logger = logging.getLogger("mail.relay")
MAX_BYTES = 2_000_000
MAX_RECIPIENTS = 5
PURPOSE = "accounts"


def authenticator(server, session, envelope, mechanism, auth_data) -> AuthResult:
    if mechanism not in ("LOGIN", "PLAIN") or not isinstance(auth_data, LoginPassword):
        return AuthResult(success=False, handled=False)
    expected_user, expected_password = settings.MAIL_RELAY_USERNAME.encode(), settings.MAIL_RELAY_PASSWORD.encode()
    ok = (bool(expected_password) and hmac.compare_digest(auth_data.login, expected_user)
          and hmac.compare_digest(auth_data.password, expected_password))
    if not ok:
        logger.warning("The mail relay refused a sign-in from %s", session.peer)
    return AuthResult(success=ok, handled=False)


def outbound_of(raw: bytes, recipients: list) -> Outbound:
    """Keycloak's message as a job: its text and HTML, to its recipients. Its Message-ID names the job, so the same
    message handed over twice is sent once."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    text_part = msg.get_body(preferencelist=("plain",))
    html_part = msg.get_body(preferencelist=("html",))
    text = text_part.get_content() if text_part is not None else ""
    html = html_part.get_content() if html_part is not None else None
    message_id = (msg.get("Message-ID") or "").strip()
    job_id = "kc-" + (hashlib.sha256(message_id.encode()).hexdigest()[:40] if message_id else uuid.uuid4().hex)
    return Outbound(id=job_id, purpose=PURPOSE, to=", ".join(recipients), subject=str(msg.get("Subject") or "")[:500],
                    text=text or (html or ""), html=html, sender_name=parseaddr(msg.get("From") or "")[0] or None,
                    message_id=message_id or None, automatic=True, meta={"from": "keycloak"})


class RelayHandler:
    def __init__(self, take: Callable[[Outbound], Awaitable[None]]):
        self.take = take

    async def handle_MAIL(self, server, session: Session, envelope: Envelope, address: str, mail_options) -> str:
        if not session.authenticated:
            return "530 5.7.0 Authentication required"
        if address.lower() != settings.MAIL_FROM.lower():
            return f"553 5.7.1 Only mail from {settings.MAIL_FROM} is accepted here"
        envelope.mail_from = address
        envelope.mail_options.extend(mail_options)
        return "250 OK"

    async def handle_RCPT(self, server, session: Session, envelope: Envelope, address: str, rcpt_options) -> str:
        if len(envelope.rcpt_tos) >= MAX_RECIPIENTS:
            return "452 4.5.3 Too many recipients"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session: Session, envelope: Envelope) -> str:
        raw = envelope.original_content or envelope.content
        if len(raw) > MAX_BYTES:
            return "552 5.3.4 Message too big"
        header_from = parseaddr(email.message_from_bytes(raw, policy=policy.default).get("From") or "")[1]
        if header_from.lower() != settings.MAIL_FROM.lower():
            return f"553 5.7.1 Only mail from {settings.MAIL_FROM} is accepted here"
        try:
            await self.take(outbound_of(raw, list(envelope.rcpt_tos)))
        except Exception:  # noqa: BLE001
            logger.exception("The mail relay couldn't take a message")
            return "451 4.3.0 Couldn't take the message, try again"
        return "250 Message accepted for delivery"


class Relay:
    def __init__(self, take: Callable[[Outbound], Awaitable[None]]):
        self.take = take
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self, host: Optional[str] = None, port: Optional[int] = None) -> Optional[int]:
        """Listens on MAIL_RELAY_HOST:MAIL_RELAY_PORT (nothing when that port is 0), or where given (port 0: any free
        port, for tests). Returns the port it listens on, or None."""
        if port is None:
            if not settings.MAIL_RELAY_PORT:
                return None
            port = settings.MAIL_RELAY_PORT
        handler = RelayHandler(self.take)
        self._server = await asyncio.get_running_loop().create_server(
            lambda: SMTP(handler, authenticator=authenticator, auth_required=True, auth_require_tls=False,
                         data_size_limit=MAX_BYTES, ident="Syruss mail relay"),
            host or settings.MAIL_RELAY_HOST, port)
        bound = self._server.sockets[0].getsockname()[1]
        logger.info("Mail relay for Keycloak listening on %s:%s", host or settings.MAIL_RELAY_HOST, bound)
        return bound

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
