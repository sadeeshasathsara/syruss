"""
S3-compatible storage (Amazon S3, MinIO, Cloudflare R2, Backblaze B2, Wasabi…): each approved record is written as a JSON file, optionally
with the original image beside it. The file name is fixed by document and revision, so a retry overwrites instead of duplicating.
"""
import asyncio
import io
import json
from typing import Any, Dict, Optional
from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import HTTPError as UrllibError
from app.services import source_data
from app.services.destinations import http
from app.services.destinations.base import Adapter, Field, SendContext, SendResult, TestResult, register
from app.services.source_data import SourceError

PERMANENT = {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "NoSuchBucket", "InvalidBucketName", "AllAccessDisabled", "AccountProblem"}


def _client(config: Dict[str, Any], secrets: Dict[str, Any]) -> Minio:
    return Minio(config["endpoint"], access_key=secrets.get("access_key", ""), secret_key=secrets.get("secret_key", ""), secure=bool(config.get("secure", True)),
                 region=config.get("region") or None)


def _key(config: Dict[str, Any], *parts: str) -> str:
    return "/".join(p.strip("/") for p in [config.get("prefix", ""), *parts] if p and p.strip("/"))


def _problem(exc: Exception) -> tuple[str, bool]:
    """(a sentence, whether trying again could help)."""
    if isinstance(exc, S3Error):
        text = {"AccessDenied": "The storage refused access. Check the keys and that they may write to this bucket.", "InvalidAccessKeyId": "The access key is not known to the storage.",
                "SignatureDoesNotMatch": "The secret key is wrong.", "NoSuchBucket": "That bucket does not exist."}.get(exc.code, f"The storage answered: {exc.code}.")
        return text, exc.code not in PERMANENT
    if isinstance(exc, (UrllibError, OSError)):
        return "The storage could not be reached.", True
    return f"The storage failed: {type(exc).__name__}.", True


class S3Adapter(Adapter):
    kind = "s3"
    label = "S3-compatible storage"
    blurb = "Write each approved record as a JSON file to a bucket (Amazon S3, MinIO, R2, Backblaze…)."
    category = "storage"
    corrections = True
    fields = [
        Field("endpoint", "Endpoint", "text", required=True, placeholder="s3.amazonaws.com", help="The storage’s host, with a port if it has one. No https://."),
        Field("bucket", "Bucket", "text", required=True),
        Field("region", "Region", "text", placeholder="eu-west-1", help="Needed by some providers; empty for MinIO."),
        Field("prefix", "Folder", "text", placeholder="syruss/approved", help="Files are written under this path."),
        Field("access_key", "Access key", "password", secret=True, required=True),
        Field("secret_key", "Secret key", "password", secret=True, required=True),
        Field("secure", "Use https", "checkbox", default=True),
        Field("include_document", "Also store the original image", "checkbox", default=False),
    ]

    async def _guard(self, config: Dict[str, Any]) -> Optional[str]:
        try:
            await source_data._check_host(f"{'https' if config.get('secure', True) else 'http'}://{config['endpoint']}")
        except SourceError as exc:
            return str(exc)
        return None

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        blocked = await self._guard(config)
        if blocked:
            return TestResult(False, blocked)

        def probe() -> None:
            client = _client(config, secrets)
            name = _key(config, ".syruss-connection-test")
            client.put_object(config["bucket"], name, io.BytesIO(b"ok"), 2, content_type="text/plain")
            client.remove_object(config["bucket"], name)

        try:
            await asyncio.to_thread(probe)
        except Exception as exc:  # noqa: BLE001
            return TestResult(False, _problem(exc)[0])
        return TestResult(True, f"Connected to {config['bucket']} and able to write files there.")

    async def send(self, ctx: SendContext) -> SendResult:
        blocked = await self._guard(ctx.config)
        if blocked:
            return SendResult(False, error=blocked, retryable=False)
        doc = ctx.envelope["document"]
        body = json.dumps(ctx.envelope, indent=2, default=str).encode()
        name = _key(ctx.config, doc["id"], f"revision-{ctx.revision}.json")
        shown = f"PUT {ctx.config['bucket']}/{name} ({len(body)} bytes)"

        def put() -> str:
            client = _client(ctx.config, ctx.secrets)
            client.put_object(ctx.config["bucket"], name, io.BytesIO(body), len(body), content_type="application/json")
            if ctx.config.get("include_document") and ctx.document_key:
                from app.services.storage_service import storage_service
                original = _key(ctx.config, doc["id"], doc.get("filename") or "document")
                image = storage_service.download_file(ctx.document_key)
                if image:
                    client.put_object(ctx.config["bucket"], original, io.BytesIO(image), len(image), content_type=doc.get("content_type") or "application/octet-stream")
            return name

        try:
            await asyncio.wait_for(asyncio.to_thread(put), timeout=60)
        except Exception as exc:  # noqa: BLE001
            text, retryable = _problem(exc)
            return SendResult(False, error=text, retryable=retryable, request=shown)
        return SendResult(True, 200, request=shown, response="Stored.", external_ref=name)


register(S3Adapter())
