"""
Mail accounts: the interface (base.py) and its kinds. `env_account()` is the one from the environment; `account_from_row`
builds a connected one from the database with its unsealed secret.
"""
from typing import Any, Dict
from mail.accounts.base import (Attachment, Capability, MailAccount, MailError, MailHandle, MailNotPermitted, Outbound,
                                SentReceipt, TestResult)
from mail.accounts.local import ConsoleAccount, MemoryAccount, NotConnectedAccount
from mail.accounts.smtp_imap import ImapServer, SmtpImapAccount, SmtpServer
from mail.config import settings

ENV_ACCOUNT_ID = "env"


def env_account() -> MailAccount:
    """What a job uses before a mailbox is connected for it: nothing (`none`: its mail waits), or for local work and tests the console
    or memory. No mail credential comes from the environment."""
    backend = settings.MAIL_BACKEND.lower()
    if backend == "memory":
        return MemoryAccount(id=ENV_ACCOUNT_ID, address=settings.MAIL_FROM, name=settings.MAIL_FROM_NAME, granted=Capability.SEND)
    if backend == "console":
        return ConsoleAccount(id=ENV_ACCOUNT_ID, address=settings.MAIL_FROM, name=settings.MAIL_FROM_NAME)
    return NotConnectedAccount(id=ENV_ACCOUNT_ID, name=settings.MAIL_FROM_NAME)


def account_from_row(row: Any, secret: Dict[str, Any], on_lost=None, on_secret=None) -> MailAccount:
    """A connected account, from its row (mail.store.AccountRow) and its unsealed secret. `on_lost(account_id, why)` is
    called when its provider stops accepting its sign-in; `on_secret(account_id, secret)` when it has a new refresh token to keep."""
    c = row.config or {}
    if row.kind == "gmail":
        from mail.accounts.gmail import GmailAccount
        return GmailAccount(id=row.id, address=row.address, name=row.name, granted=Capability.parse(row.granted),
                            refresh_token=secret.get("refresh_token", ""), on_lost=on_lost)
    if row.kind == "graph":
        from mail.accounts.graph import GraphAccount
        return GraphAccount(id=row.id, address=row.address, name=row.name, granted=Capability.parse(row.granted),
                            refresh_token=secret.get("refresh_token", ""), on_lost=on_lost, on_secret=on_secret)
    if row.kind == "smtp_imap":
        smtp = SmtpServer(c["smtp_host"], int(c.get("smtp_port", 587)), c.get("smtp_username", ""), secret.get("smtp_password", ""),
                          c.get("smtp_security", "starttls")) if c.get("smtp_host") else None
        imap = ImapServer(c["imap_host"], int(c.get("imap_port", 993)), c.get("imap_username", ""), secret.get("imap_password", ""),
                          c.get("imap_folder", "INBOX"), bool(c.get("imap_ssl", True))) if c.get("imap_host") else None
        return SmtpImapAccount(id=row.id, address=row.address, name=row.name, smtp=smtp, imap=imap)
    raise ValueError(f"Unknown mail account kind '{row.kind}'.")


__all__ = ["Attachment", "Capability", "ConsoleAccount", "ENV_ACCOUNT_ID", "ImapServer", "MailAccount", "MailError", "MailHandle",
           "MailNotPermitted", "MemoryAccount", "NotConnectedAccount", "Outbound", "SentReceipt", "SmtpImapAccount", "SmtpServer", "TestResult",
           "account_from_row", "env_account"]
