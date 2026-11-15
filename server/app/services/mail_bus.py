"""
The server's only line to the mail service, and it is Kafka: the server holds no mail setting, no address of a mail server and
no credential. (mail/ explains its side: mail/commands.py, mail/directory.py.)

    commands      `command()` signs a command (Ed25519, MAIL_COMMAND_SIGNING_KEY) and puts it on mail.commands, then waits for the
                  answer on mail.replies by its id. The mail service carries out only what is signed, recent and new.
    directory     what the mail service says about itself, on the compacted topic mail.directory: its jobs, accounts (address, kind,
                  status; never a secret) and the public key people seal secrets to. Kept as a read model here; the admin app's
                  Email page and the support mailbox's addresses are read from it.
    inbound       mail the service read, whole on mail.inbound; taken in (support tickets, documents), then acknowledged on
                  mail.inbound.acks so the service lets it go. One it never hears of is published again.

Without Kafka, or the mail service, nothing breaks for other features: mail waits in the outbox, an admin action answers
"the mail service didn't answer", and the last directory stays in use.
"""
import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

try:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
except ImportError:  # pragma: no cover
    AIOKafkaConsumer = AIOKafkaProducer = None

logger = logging.getLogger("services.mail_bus")
SEAL_INFO = b"syruss-mail-seal-v1"
Listener = Callable[[str, Optional[Dict[str, Any]]], Awaitable[None]]


class MailBusError(Exception):
    """Something the mail service refused or couldn't do, in words an owner can read. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# ---- the formats shared with mail/ (a test vector in both test suites keeps them identical) -----------

def canonical(envelope: Dict[str, Any]) -> bytes:
    """The bytes that are signed: everything but `sig`, keys sorted, no spaces (mail/commands.py: canonical)."""
    body = {k: envelope[k] for k in ("id", "type", "ts", "by", "payload") if k in envelope}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(private: bytes, message: bytes) -> bytes:
    return ed25519.Ed25519PrivateKey.from_private_bytes(private).sign(message)


def box_seal(recipient_public: bytes, data: bytes, ephemeral_private: Optional[bytes] = None, nonce: Optional[bytes] = None) -> bytes:
    """Seals `data` so only the mail service can open it (mail/sealing.py: box_seal): ephemeral public key (32) | nonce (12) | ciphertext+tag."""
    import os
    ephemeral = x25519.X25519PrivateKey.from_private_bytes(ephemeral_private) if ephemeral_private else x25519.X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    secret = ephemeral.exchange(x25519.X25519PublicKey.from_public_bytes(recipient_public))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=ephemeral_public + recipient_public, info=SEAL_INFO).derive(secret)
    nonce = nonce or os.urandom(12)
    return ephemeral_public + nonce + AESGCM(key).encrypt(nonce, data, SEAL_INFO)


def new_signing_keypair() -> tuple:
    """(private, public), 32 bytes each: the server keeps the private key; the mail service is given the public one."""
    private = ed25519.Ed25519PrivateKey.generate()
    return (private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()),
            private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


# ---- the bus -----------------------------------------------------------------------------------------

class MailBus:
    def __init__(self):
        self.state: Dict[str, Dict[str, Any]] = {}          # the directory: key -> its latest value
        self.pending: Dict[str, "asyncio.Future"] = {}      # commands waiting for their answer
        self.listeners: List[Listener] = []
        self.loaded = asyncio.Event()                       # the directory has been read up to now
        self._producer = None
        self._tasks: List[asyncio.Task] = []

    # -- life ---------------------------------------------------------------------------------------

    def enabled(self) -> bool:
        return settings.MAIL_BACKEND.lower() == "service" and AIOKafkaConsumer is not None

    async def start(self) -> None:
        if not self.enabled():
            return
        self._tasks = [asyncio.create_task(self._watch(settings.MAIL_REPLIES_TOPIC, self._replies)),
                       asyncio.create_task(self._watch(settings.MAIL_DIRECTORY_TOPIC, self._directory, mark_loaded=True))]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def _watch(self, topic: str, handle, mark_loaded: bool = False) -> None:
        """Reads a topic (its own consumer group, from the start: the directory is rebuilt, replies for others are ignored)."""
        while True:
            consumer = None
            try:
                consumer = AIOKafkaConsumer(topic, bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, group_id=f"core-{topic}-{uuid.uuid4().hex[:8]}",
                                            auto_offset_reset="earliest", enable_auto_commit=False)
                await consumer.start()
                while True:
                    batch = await consumer.getmany(timeout_ms=1000)
                    for messages in batch.values():
                        for m in messages:
                            try:
                                await handle(m.key.decode() if m.key else "", m.value)
                            except Exception:  # noqa: BLE001  (one bad message must not stop the rest)
                                logger.exception("Couldn't take in a message from %s", topic)
                    if mark_loaded and not batch:
                        self.loaded.set()      # nothing more to read right now: the directory is up to date
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s unavailable (%s); trying again shortly", topic, exc)
                await asyncio.sleep(10)
            finally:
                if consumer is not None:
                    try:
                        await consumer.stop()
                    except Exception:  # noqa: BLE001
                        pass

    # -- the producer ---------------------------------------------------------------------------------

    async def producer(self):
        if self._producer is None:
            if AIOKafkaProducer is None:
                raise MailBusError("Kafka isn't available on this server.", 503)
            try:
                producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, acks="all", max_request_size=2_097_152)
                await producer.start()
            except Exception as e:  # noqa: BLE001
                raise MailBusError("The mail service can't be reached right now (Kafka didn't answer).", 502) from e
            self._producer = producer
        return self._producer

    async def send(self, topic: str, key: str, value: bytes, headers: Optional[list] = None) -> None:
        producer = await self.producer()
        await producer.send_and_wait(topic, key=key.encode(), value=value, headers=headers)

    # -- commands -------------------------------------------------------------------------------------

    def _signing_key(self) -> bytes:
        try:
            key = base64.b64decode(settings.MAIL_COMMAND_SIGNING_KEY)
        except ValueError:
            key = b""
        if len(key) != 32:
            raise MailBusError("This server can't sign commands for the mail service: MAIL_COMMAND_SIGNING_KEY isn't set "
                               "(python -m app.mail_signing_key makes one).", 503)
        return key

    async def command(self, kind: str, payload: Optional[Dict[str, Any]] = None, by: str = "", timeout: float = 20.0) -> Dict[str, Any]:
        """Has the mail service do something and returns what it answers. Raises MailBusError (with the reason) when it is refused,
        can't be reached or doesn't answer."""
        if settings.MAIL_BACKEND.lower() != "service":
            raise MailBusError("Mail isn't going through the mail service on this server (MAIL_BACKEND).", 409)
        private = self._signing_key()
        envelope: Dict[str, Any] = {"id": uuid.uuid4().hex, "type": kind, "ts": time.time(), "by": by, "payload": payload or {}}
        envelope["sig"] = base64.b64encode(sign(private, canonical(envelope))).decode()
        future = asyncio.get_running_loop().create_future()
        self.pending[envelope["id"]] = future
        try:
            await self.send(settings.MAIL_COMMANDS_TOPIC, envelope["id"], json.dumps(envelope).encode())
            reply = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise MailBusError("The mail service didn't answer. Is it running?", 504) from None
        finally:
            self.pending.pop(envelope["id"], None)
        if not reply.get("ok"):
            raise MailBusError(str(reply.get("error") or "The mail service refused that."), 422)
        return reply.get("data") or {}

    async def _replies(self, key: str, value: Optional[bytes]) -> None:
        reply = json.loads(value or b"{}")
        future = self.pending.get(reply.get("id"))
        if future is not None and not future.done():
            future.set_result(reply)

    # -- the directory --------------------------------------------------------------------------------

    async def _directory(self, key: str, value: Optional[bytes]) -> None:
        if value is None:
            if key not in self.state:
                return
            self.state.pop(key, None)
            entry = None
        else:
            entry = json.loads(value)
            if self.state.get(key) == entry:
                return          # the mail service publishes everything every minute: nothing new, nobody is told
            self.state[key] = entry
        for listener in list(self.listeners):
            try:
                await listener(key, entry)
            except Exception:  # noqa: BLE001
                logger.exception("A directory listener failed")

    async def ready(self, timeout: float = 5.0) -> bool:
        """Waits (a little) for the directory to be read. False when the mail service hasn't been heard from."""
        try:
            await asyncio.wait_for(self.loaded.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return bool(self.state)

    def entries(self, prefix: str) -> List[Dict[str, Any]]:
        return [v for k, v in self.state.items() if k.startswith(prefix)]

    @property
    def meta(self) -> Dict[str, Any]:
        return self.state.get("meta", {})

    def seal(self, secrets: Dict[str, Any]) -> str:
        """Secrets sealed to the mail service's public key (what the browser will do itself; this is for a caller that has the
        secret in hand). Raises when the service hasn't published its key."""
        public = self.meta.get("public_key")
        if not public:
            raise MailBusError("The mail service hasn't told this server its key yet. Try again in a moment.", 503)
        return base64.b64encode(box_seal(base64.b64decode(public), json.dumps(secrets).encode())).decode()

    def overview(self) -> Dict[str, Any]:
        """What the admin app's Email page shows, from the directory: each job's account, every account (no secrets), which providers can be connected."""
        jobs = {}
        for j in self.entries("job:"):
            account = j.get("account") or {}
            jobs[j["key"]] = {"account_id": account.get("id"), "address": account.get("address"), "routed": j.get("routed", False),
                              "needs": j.get("needs", []), "reads": j.get("reads", False), "label": j.get("label"),
                              "direction": j.get("direction"), "system": j.get("system", False), "description": j.get("description", ""),
                              "settings": j.get("settings", {}), "last_sent_at": j.get("last_sent_at"),
                              "last_read_at": j.get("last_read_at"), "last_error": j.get("last_error")}
        accounts = []
        for a in sorted(self.entries("account:"), key=lambda a: (not a.get("from_environment"), a.get("created_at") or "")):
            accounts.append({**a, "config": a.get("hosts", {}), "purposes": a.get("jobs", [])})
        providers = {p: (v if isinstance(v, dict) else {"configured": bool(v)}) for p, v in (self.meta.get("providers") or {}).items()}
        for p in ("google", "microsoft"):
            providers.setdefault(p, {"configured": False})
        return {"purposes": jobs, "google": bool(providers["google"].get("configured")), "providers": providers, "accounts": accounts,
                "key": {"fingerprint": self.meta.get("fingerprint"), "public_key": self.meta.get("public_key")}}


bus = MailBus()
