"""
Documents sent over SFTP, for scanners and older systems that can only drop a file on a server. A channel of kind "sftp" has its own user name
and a password (or an SSH public key). Each person who connects gets an empty folder of their own that exists only for the session: files can
be written into it, never read or listed back, and each file is checked and queued the moment it is closed, exactly like a file sent through the
API. A refused file makes the upload fail, with the reason as the SFTP error message. There is no shell and no forwarding.
"""
import asyncio
import hashlib
import hmac
import logging
import os
import secrets as token
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
import asyncssh
from asyncssh import SFTPFailure, SFTPPermissionDenied
from sqlalchemy import select
from app.core import ratelimit
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.intake import IntakeChannel
from app.services import intake_service
from app.services.intake_rules import IntakeRejected

logger = logging.getLogger("core.intake.sftp")
FXF_READ = 0x01


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        return hmac.compare_digest(hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32).hex(), digest)
    except ValueError:
        return False


def new_credentials(slug: str) -> Dict[str, str]:
    """A user name and a password for a new SFTP channel. The password is shown once; only its hash is kept."""
    password = token.token_urlsafe(18)
    return {"sftp_username": f"{slug[:14].strip('-') or 'scan'}-{token.token_hex(3)}", "password": password, "sftp_secret_hash": hash_password(password)}


def public_host() -> str:
    from urllib.parse import urlsplit
    return settings.INTAKE_SFTP_PUBLIC_HOST or urlsplit(settings.PUBLIC_API_URL).hostname or "localhost"


def valid_public_key(text: str) -> Optional[str]:
    """The key as an OpenSSH line, or a sentence about what is wrong with it."""
    try:
        asyncssh.import_public_key(text.strip())
    except (asyncssh.KeyImportError, ValueError):
        return "That is not an SSH public key. Paste the whole line, such as ssh-ed25519 AAAA… name."
    return None


async def channel_for_username(username: str) -> Optional[IntakeChannel]:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(IntakeChannel).where(IntakeChannel.kind == "sftp", IntakeChannel.config["sftp_username"].as_string() == username))).scalar_one_or_none()


class SftpAccess(asyncssh.SSHServer):
    """Who may connect: the channel's user name with its password or key. Wrong tries are limited by address and by user name."""

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        self._conn = conn
        peer = conn.get_extra_info("peername")
        self._ip = peer[0] if peer else ""

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def public_key_auth_supported(self) -> bool:
        return True

    def _allowed(self, username: str) -> bool:
        try:
            ratelimit.limit(f"sftp-ip:{self._ip}", 20, 60)
            ratelimit.limit(f"sftp-user:{username}", 10, 60)
        except Exception:  # noqa: BLE001  (too many tries)
            return False
        return True

    def _accept(self, channel: IntakeChannel, username: str) -> bool:
        if channel.status != "active":
            return False
        self._conn.set_extra_info(channel_id=channel.id, sftp_user=username, client_ip=self._ip)
        return True

    async def validate_password(self, username: str, password: str) -> bool:
        if not self._allowed(username):
            return False
        channel = await channel_for_username(username)
        stored = (channel.config or {}).get("sftp_secret_hash") if channel else None
        ok = check_password(password, stored or hash_password("no-such-user"))   # (work is done either way: no hint which user names exist)
        return bool(channel and stored and ok and self._accept(channel, username))

    async def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        if not self._allowed(username):
            return False
        channel = await channel_for_username(username)
        text = (channel.config or {}).get("sftp_public_key") if channel else None
        if not text:
            return False
        for line in text.splitlines():
            try:
                if asyncssh.import_public_key(line.strip()).public_data == key.public_data:
                    return self._accept(channel, username)
            except (asyncssh.KeyImportError, ValueError):
                continue
        return False


class IntakeSftp(asyncssh.SFTPServer):
    """A folder that exists for one session. Writing a file into it sends the file in; nothing can be read or listed back."""

    def __init__(self, chan: asyncssh.SSHServerChannel):
        conn = chan.get_connection()
        self.channel_id: str = conn.get_extra_info("channel_id")
        self.user: str = conn.get_extra_info("sftp_user")
        self.ip: str = conn.get_extra_info("client_ip") or ""
        self._root = tempfile.mkdtemp(prefix="syruss-sftp-")
        self._opened: Dict[Any, str] = {}
        super().__init__(chan, chroot=self._root.encode())

    def exit(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def _flat(self, path: bytes) -> bytes:
        name = os.path.basename(path.decode(errors="replace").replace("\\", "/"))
        if not name or name in (".", ".."):
            raise SFTPPermissionDenied("Files can only be written into the top folder.")
        return b"/" + name.encode()

    def open(self, path: bytes, pflags: int, attrs):
        if pflags & FXF_READ:
            raise SFTPPermissionDenied("This server only receives files; it does not send them back.")
        flat = self._flat(path)
        handle = super().open(flat, pflags, attrs)
        self._opened[handle] = flat.decode()
        return handle

    def mkdir(self, path, attrs):
        raise SFTPPermissionDenied("Folders cannot be made here.")

    def scandir(self, path):
        async def nothing():
            return
            yield  # noqa: unreachable  (an empty listing)
        return nothing()

    def rename(self, oldpath, newpath):
        raise SFTPPermissionDenied("Files cannot be renamed here.")

    posix_rename = rename

    async def close(self, file_obj) -> None:
        super().close(file_obj)
        name = self._opened.pop(file_obj, None)
        if name is None:
            return
        path = Path(self._root) / name.lstrip("/")
        try:
            content = path.read_bytes() if path.stat().st_size <= 120_000_000 else b""
        finally:
            path.unlink(missing_ok=True)
        if not content:
            raise SFTPFailure("The file is empty or larger than 120 MB.")
        sender = intake_service.Sender(label=self.user, via="sftp", ip=self.ip)
        try:
            await intake_service.ingest(self.channel_id, content=content, filename=name.lstrip("/"), sender=sender,
                                        external_id=f"{self.user}/{name.lstrip('/')}/{hashlib.sha1(content).hexdigest()[:12]}")
        except IntakeRejected as rejected:
            raise SFTPFailure(rejected.reason)


def _host_key() -> asyncssh.SSHKey:
    """The server's identity. Kept in a file so senders see the same key every time it starts."""
    path = Path(settings.INTAKE_SFTP_HOST_KEY or "sftp_host_key")
    if path.exists():
        return asyncssh.read_private_key(str(path))
    key = asyncssh.generate_private_key("ssh-ed25519")
    path.write_bytes(key.export_private_key())
    try:
        path.chmod(0o600)
    except OSError:
        pass
    logger.warning("Made a new SFTP host key at %s. Back it up: senders will see a warning if it changes.", path)
    return key


class SftpReceiver:
    def __init__(self) -> None:
        self._acceptor: Optional[asyncssh.SSHAcceptor] = None
        self.port: Optional[int] = None

    async def start(self) -> None:
        if settings.INTAKE_SFTP_PORT:
            await self.listen(settings.INTAKE_SFTP_HOST, settings.INTAKE_SFTP_PORT)

    async def listen(self, host: str, port: int, host_key: Optional[asyncssh.SSHKey] = None) -> int:
        if self._acceptor is None:
            self._acceptor = await asyncssh.listen(
                host, port, server_factory=SftpAccess, server_host_keys=[host_key or _host_key()], sftp_factory=IntakeSftp,
                allow_scp=False, line_editor=False, login_timeout=30,
            )
            self.port = self._acceptor.get_port()
            logger.info("SFTP intake listening on port %s", self.port)
        return self.port or 0

    async def stop(self) -> None:
        if self._acceptor is not None:
            self._acceptor.close()
            await self._acceptor.wait_closed()
            self._acceptor = None


sftp_receiver = SftpReceiver()
