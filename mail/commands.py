"""
Commands: how the server (for an owner in the admin app) changes anything here. A command arrives on mail.commands as JSON:

    {"id": "<unique>", "type": "account.add_imap", "ts": <unix seconds>, "by": "<the staff member>", "payload": {...}, "sig": "<base64>"}

`sig` is an Ed25519 signature, by the server's private key, of the canonical form of everything but `sig`
(`canonical()`: the JSON with sorted keys and no spaces, UTF-8). Only a command that

    - is signed by the key in MAIL_TRUSTED_SERVER_KEY (Kafka itself has no login, so anyone able to write to the topic could
      otherwise add a mailbox),
    - is not older (or newer) than COMMAND_MAX_AGE_SECONDS,
    - has an id never seen before (a replay is refused),

is carried out. The answer goes on mail.replies with the same id: {"id", "type", "ok", "data" | "error"}. Neither it nor
anything else this module publishes ever holds a secret: passwords arrive sealed to this service's public key
(`sealed`, base64 of sealing.box_seal) and are opened here, kept sealed under the master key, and never come back out.

    directory.refresh                 publish the directory again
    account.add_imap                  connect a mail server (SMTP to send, IMAP to read); optional `jobs` it serves
    account.test                      sign in and check, for what a job needs      {account_id, purpose?}
    account.remove                    remove an account and what it served          {account_id}
    job.route                         which account serves a job (null: the environment's)   {purpose, account_id}
    job.create / job.update / job.delete                                            {key, label, direction, ...}
    jobs.log                          the delivery log                              {status?, limit?}
    job.retry                         send a given-up message again                 {id}
    oauth.<provider>.start / .finish  connect Google or Microsoft                   {jobs, redirect_uri} / {code (sealed), state, redirect_uri}
    provider_app.set / .clear         the OAuth app a provider gave Syruss            {provider, client_id, tenant?, sealed: {client_secret}} / {provider}
"""
import base64
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from mail import apps, sealing
from mail.config import settings
from mail.operations import OperationError, Operations
from mail.store import Store

logger = logging.getLogger("mail.commands")
Reply = Callable[[Dict[str, Any]], Awaitable[None]]
Changed = Callable[[], Awaitable[None]]

# Changes that make the directory stale (the others only read)
CHANGES = {"account.add_imap", "account.remove", "account.test", "job.route", "job.create", "job.update", "job.delete",
           "oauth.google.finish", "oauth.microsoft.finish", "provider_app.set", "provider_app.clear", "directory.refresh"}


def canonical(envelope: Dict[str, Any]) -> bytes:
    """The bytes that are signed: everything but `sig`, keys sorted, no spaces."""
    body = {k: envelope[k] for k in ("id", "type", "ts", "by", "payload") if k in envelope}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class Commands:
    def __init__(self, store: Store, ops: Operations, reply: Reply, changed: Optional[Changed] = None):
        self.store, self.ops, self.reply, self.changed = store, ops, reply, changed
        self.handlers = {
            "directory.refresh": self._refresh,
            "account.add_imap": self._add_imap,
            "account.test": self._test,
            "account.remove": self._remove,
            "job.route": self._route,
            "job.create": self._job_create,
            "job.update": self._job_update,
            "job.delete": self._job_delete,
            "jobs.log": self._log,
            "job.retry": self._retry,
            "provider_app.set": self._app_set,
            "provider_app.clear": self._app_clear,
        }
        for provider in apps.PROVIDERS:
            self.handlers[f"oauth.{provider}.start"] = self._oauth_start(provider)
            self.handlers[f"oauth.{provider}.finish"] = self._oauth_finish(provider)

    async def handle(self, raw: bytes) -> Optional[Dict[str, Any]]:
        """One message from mail.commands. Returns the reply it published, or None when there was nobody to answer."""
        try:
            envelope = json.loads(raw)
            command_id, kind = str(envelope["id"]), str(envelope["type"])
            sig = base64.b64decode(envelope["sig"])
            ts = float(envelope["ts"])
        except (ValueError, KeyError, TypeError):
            logger.warning("Ignored a malformed command")
            return None

        if not settings.MAIL_TRUSTED_SERVER_KEY:
            logger.error("Refused command %s (%s): MAIL_TRUSTED_SERVER_KEY isn't set, so no command can be trusted", command_id, kind)
            return await self._answer(command_id, kind, False, error="This mail service isn't set up to take commands yet.")
        if not sealing.verify(settings.MAIL_TRUSTED_SERVER_KEY, canonical(envelope), sig):
            logger.error("Refused command %s (%s): the signature is wrong. Someone is writing to mail.commands who isn't the server, "
                         "or the server's signing key and MAIL_TRUSTED_SERVER_KEY differ.", command_id, kind)
            return await self._answer(command_id, kind, False, error="Not signed by the server.")
        if abs(time.time() - ts) > settings.COMMAND_MAX_AGE_SECONDS:
            logger.warning("Refused command %s (%s): sent %ds ago", command_id, kind, int(time.time() - ts))
            return await self._answer(command_id, kind, False, error="That command is too old. Try again.")
        by = str(envelope.get("by") or "")
        if not await self.store.record_command(command_id, kind, by):
            logger.warning("Refused command %s (%s): already carried out", command_id, kind)
            return await self._answer(command_id, kind, False, error="That command was already received.")
        handler = self.handlers.get(kind)
        if handler is None:
            await self.store.finish_command(command_id, False, "unknown")
            return await self._answer(command_id, kind, False, error=f"This mail service doesn't know '{kind}'.")

        payload = envelope.get("payload") or {}
        try:
            data = await handler(payload if isinstance(payload, dict) else {}, by)
        except OperationError as e:
            await self.store.finish_command(command_id, False, str(e))
            return await self._answer(command_id, kind, False, error=str(e))
        except Exception:  # noqa: BLE001  (never echo what went wrong inside: it could name a secret)
            logger.exception("Command %s (%s) failed", command_id, kind)
            await self.store.finish_command(command_id, False, "failed")
            return await self._answer(command_id, kind, False, error="That didn't work. The mail service logged why.")
        await self.store.finish_command(command_id, True)
        logger.info("Carried out %s for %s", kind, by or "?")
        if kind in CHANGES and self.changed is not None:
            try:
                await self.changed()
            except Exception:  # noqa: BLE001
                logger.exception("Couldn't publish the directory after %s", kind)
        return await self._answer(command_id, kind, True, data=data)

    async def _answer(self, command_id: str, kind: str, ok: bool, data: Any = None, error: Optional[str] = None) -> Dict[str, Any]:
        reply = {"id": command_id, "type": kind, "ok": ok}
        if ok:
            reply["data"] = data if data is not None else {}
        else:
            reply["error"] = error or "Refused."
        await self.reply(reply)
        return reply

    # ---- the handlers (each returns what goes back as `data`: public things only) ------------------------------

    async def _sealed(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        blob = payload.get("sealed")
        if not blob:
            return {}
        try:
            opened = json.loads(await self.store.open_box(base64.b64decode(blob)))
        except (ValueError, TypeError) as e:
            raise OperationError("The secret couldn't be opened. Reload the page and try again (the mail service's key may have changed).") from e
        return opened if isinstance(opened, dict) else {}

    async def _refresh(self, payload, by):
        return {}

    async def _add_imap(self, payload, by):
        secret = await self._sealed(payload)
        config = {k: v for k, v in payload.items() if k.startswith(("smtp_", "imap_")) and "password" not in k and v not in (None, "")}
        row = await self.ops.add_imap(
            by=by, address=str(payload.get("address", "")), name=str(payload.get("name", "Syruss")), config=config,
            secret={"smtp_password": str(secret.get("smtp_password", "")), "imap_password": str(secret.get("imap_password", ""))},
            jobs=[str(j) for j in payload.get("jobs") or []])
        return {"id": row.id, "address": row.address, "granted": row.granted}

    async def _test(self, payload, by):
        return await self.ops.test(str(payload.get("account_id", "")), payload.get("purpose"))

    async def _remove(self, payload, by):
        await self.ops.remove(str(payload.get("account_id", "")))
        return {}

    async def _route(self, payload, by):
        await self.ops.route(str(payload.get("purpose", "")), payload.get("account_id"))
        return {}

    async def _job_create(self, payload, by):
        job = await self.ops.create_job(str(payload.get("key", "")), str(payload.get("label", "")), str(payload.get("direction", "")),
                                        str(payload.get("description", "")), payload.get("settings"))
        return {"key": job.key}

    async def _job_update(self, payload, by):
        job = await self.ops.update_job(str(payload.get("key", "")), payload.get("label"), payload.get("description"), payload.get("settings"))
        return {"key": job.key}

    async def _job_delete(self, payload, by):
        await self.ops.delete_job(str(payload.get("key", "")))
        return {}

    async def _log(self, payload, by):
        return {"jobs": await self.ops.delivery_log(payload.get("status"), int(payload.get("limit") or 100))}

    async def _retry(self, payload, by):
        return await self.ops.retry(str(payload.get("id", "")))

    def _oauth_start(self, provider: str):
        async def start(payload, by):
            return {"url": await self.ops.oauth_start(provider, [str(j) for j in payload.get("jobs") or []], str(payload.get("redirect_uri", "")), by)}
        return start

    def _oauth_finish(self, provider: str):
        async def finish(payload, by):
            secret = await self._sealed(payload)
            code = str(secret.get("code") or payload.get("code") or "")
            return await self.ops.oauth_finish(provider, code, str(payload.get("state", "")), str(payload.get("redirect_uri", "")), by)
        return finish

    async def _app_set(self, payload, by):
        secret = await self._sealed(payload)
        await self.ops.app_set(str(payload.get("provider", "")), str(payload.get("client_id", "")), str(secret.get("client_secret", "")),
                               str(payload.get("tenant", "")))
        return {}

    async def _app_clear(self, payload, by):
        await self.ops.app_clear(str(payload.get("provider", "")))
        return {}
