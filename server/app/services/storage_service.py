import io
from datetime import timedelta
from typing import Optional, BinaryIO
from minio import Minio
from minio.error import S3Error
from app.core.config import settings


class StorageService:
    def __init__(self):
        self.client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ROOT_USER,
            secret_key=settings.MINIO_ROOT_PASSWORD,
            secure=settings.MINIO_SECURE
        )
        self.bucket_name = settings.MINIO_BUCKET_NAME

    def ensure_bucket_exists(self) -> None:
        """Create the documents bucket if it does not already exist."""
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except S3Error as e:
            print(f"MinIO bucket initialization error: {e}")
            raise

    def upload_file(
        self,
        object_name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        length: Optional[int] = None,
        **kwargs
    ) -> str:
        """Upload raw bytes to MinIO and return the object path."""
        data_stream = io.BytesIO(data)
        self.client.put_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            data=data_stream,
            length=len(data),
            content_type=content_type
        )
        return object_name

    def download_file(self, object_name: str) -> bytes:
        """Download file bytes from MinIO."""
        response = None
        try:
            response = self.client.get_object(self.bucket_name, object_name)
            return response.read()
        finally:
            if response:
                response.close()
                response.release_conn()

    def get_presigned_url(self, object_name: str, expires_minutes: int = 60) -> str:
        """Generate a presigned GET URL for downloading."""
        return self.client.presigned_get_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            expires=timedelta(minutes=expires_minutes)
        )

    # --- direct uploads by outside senders -------------------------------------------------------------
    def _public_client(self) -> Minio:
        """A client signed for the address outside senders reach storage on (a presigned link is bound to its host)."""
        endpoint = settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT
        secure = settings.MINIO_PUBLIC_SECURE if settings.MINIO_PUBLIC_ENDPOINT else settings.MINIO_SECURE
        # The region is given so signing never asks the storage server (which an outside address may not reach from here).
        return Minio(endpoint=endpoint, access_key=settings.MINIO_ROOT_USER, secret_key=settings.MINIO_ROOT_PASSWORD, secure=secure, region="us-east-1")

    def presigned_upload_form(self, object_name: str, *, content_type: str, max_bytes: int, expires_minutes: int) -> dict:
        """
        A browser/HTTP form post that stores one object under this exact name, of this type, no larger than max_bytes: storage itself
        enforces all three, so a sender cannot use the link for anything else.
        """
        from minio.datatypes import PostPolicy
        from datetime import datetime, timezone
        policy = PostPolicy(self.bucket_name, datetime.now(timezone.utc) + timedelta(minutes=expires_minutes))
        policy.add_equals_condition("key", object_name)
        policy.add_equals_condition("Content-Type", content_type)
        policy.add_content_length_range_condition(1, max_bytes)
        client = self._public_client()
        fields = client.presigned_post_policy(policy)
        scheme = "https" if (settings.MINIO_PUBLIC_SECURE if settings.MINIO_PUBLIC_ENDPOINT else settings.MINIO_SECURE) else "http"
        url = f"{scheme}://{settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT}/{self.bucket_name}"
        return {"url": url, "fields": {**fields, "key": object_name, "Content-Type": content_type}}

    def object_size(self, object_name: str) -> Optional[int]:
        """The size of a stored object, or None when it is not there."""
        try:
            return self.client.stat_object(self.bucket_name, object_name).size
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
                return None
            raise

    def read_object(self, object_name: str, max_bytes: int) -> bytes:
        """The object's bytes, refusing to read more than max_bytes."""
        response = None
        try:
            response = self.client.get_object(self.bucket_name, object_name)
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("The object is larger than allowed.")
            return data
        finally:
            if response:
                response.close()
                response.release_conn()

    def remove_object(self, object_name: str) -> None:
        try:
            self.client.remove_object(self.bucket_name, object_name)
        except S3Error:
            pass


storage_service = StorageService()
