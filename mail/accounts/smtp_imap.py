"""
A mailbox on any mail server: SMTP to send, IMAP to read. Reading never changes the mailbox (read-only SELECT,
BODY.PEEK), so it can be a shared inbox; the cursor is the folder's UIDVALIDITY and the last UID read.
"""
import asyncio
import imaplib
import logging
import smtplib
import ssl
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from mail.accounts.base import Capability, MailAccount, MailError, Outbound, SentReceipt, TestResult
from mail.accounts.mime import build

logger = logging.getLogger("mail.smtp_imap")
TIMEOUT = 20
BATCH = 25


@dataclass(frozen=True)
class SmtpServer:
    host: str
    port: int = 587
    username: str = ""
    password: str = ""
    security: str = "starttls"  # none | starttls | ssl


@dataclass(frozen=True)
class ImapServer:
    host: str
    port: int = 993
    username: str = ""
    password: str = ""
    folder: str = "INBOX"
    ssl: bool = True


class SmtpImapAccount(MailAccount):
    kind = "smtp_imap"
    supports = Capability.READ | Capability.SEND

    def __init__(self, *, id: str, address: str, name: str, smtp: Optional[SmtpServer] = None, imap: Optional[ImapServer] = None):
        granted = (Capability.SEND if smtp and smtp.host else Capability.NONE) | (Capability.READ if imap and imap.host else Capability.NONE)
        super().__init__(id=id, address=address, name=name, granted=granted)
        self.smtp, self.imap = smtp, imap

    # ---- sending -------------------------------------------------------------------------------------

    def _deliver(self, raw) -> None:
        via = self.smtp
        security = via.security.lower()
        try:
            if security == "ssl":
                server = smtplib.SMTP_SSL(via.host, via.port, timeout=TIMEOUT, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(via.host, via.port, timeout=TIMEOUT)
            with server:
                if security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                if via.username:
                    server.login(via.username, via.password)
                server.send_message(raw)
        except smtplib.SMTPAuthenticationError as e:
            raise MailError("The mail server refused our sign-in.") from e
        except smtplib.SMTPRecipientsRefused as e:
            raise MailError("The mail server refused that address.", permanent=True) from e
        except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
            raise MailError("The mail server couldn't be reached or didn't accept the message.") from e

    async def _send(self, message: Outbound) -> SentReceipt:
        mime = build(message, self.address, self.name)
        await asyncio.to_thread(self._deliver, mime)
        return SentReceipt(provider_id=mime["Message-ID"])

    # ---- reading -------------------------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4:
        s = self.imap
        conn = imaplib.IMAP4_SSL(s.host, s.port, timeout=TIMEOUT) if s.ssl else imaplib.IMAP4(s.host, s.port, timeout=TIMEOUT)
        if not s.ssl:
            conn.starttls()
        conn.login(s.username, s.password)
        conn.select(s.folder, readonly=True)  # read only: the mailbox stays as the team left it
        return conn

    def _read(self, cursor: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[bytes]]:
        try:
            conn = self._connect()
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as e:
            raise MailError("The mailbox couldn't be reached or refused our sign-in.") from e
        try:
            validity = int((conn.response("UIDVALIDITY")[1] or [b"0"])[0] or 0)
            last = int((cursor or {}).get("last_uid", 0))
            fresh = cursor is None or cursor.get("validity") != validity  # first look, or the folder was rebuilt
            _, data = conn.uid("search", None, f"UID {last + 1 if not fresh else 1}:*")
            uids = sorted(int(u) for u in (data[0] or b"").split())
            if fresh:  # leave what was already there; read what arrives from now on
                return {"validity": validity, "last_uid": max(uids, default=0)}, []
            uids = [u for u in uids if u > last][:BATCH]
            out: List[bytes] = []
            for uid in uids:
                _, parts = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple) and len(p) > 1), None)
                if raw:
                    out.append(raw)
            return {"validity": validity, "last_uid": max(uids, default=last)}, out
        except (imaplib.IMAP4.error, OSError) as e:
            raise MailError("The mailbox couldn't be read.") from e
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    async def _fetch(self, cursor: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[bytes]]:
        return await asyncio.to_thread(self._read, cursor)

    # ---- checking ------------------------------------------------------------------------------------

    def _check(self, needs: Capability) -> TestResult:
        try:
            if Capability.SEND in needs:
                s = self.smtp
                server = (smtplib.SMTP_SSL(s.host, s.port, timeout=TIMEOUT, context=ssl.create_default_context()) if s.security == "ssl"
                          else smtplib.SMTP(s.host, s.port, timeout=TIMEOUT))
                with server:
                    if s.security == "starttls":
                        server.starttls(context=ssl.create_default_context())
                    if s.username:
                        server.login(s.username, s.password)
            if Capability.READ in needs:
                self._connect().logout()
        except smtplib.SMTPAuthenticationError:
            return TestResult(False, "The mail server refused our sign-in.")
        except (smtplib.SMTPException, imaplib.IMAP4.error, OSError, ssl.SSLError) as e:
            return TestResult(False, f"Couldn't connect: {type(e).__name__}.")
        return TestResult(True, "Connected and signed in.")

    async def _test(self, needs: Capability) -> TestResult:
        return await asyncio.to_thread(self._check, needs)
