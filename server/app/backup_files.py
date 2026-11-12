"""
Stored files in and out of a backup, as one tar stream. Runs inside the server container, which already reaches the
object storage with its own settings:

    python -m app.backup_files dump    > files.tar      every object in MINIO_BUCKET_NAME, with its content type
    python -m app.backup_files restore < files.tar      puts them back (objects of the same name are replaced)

deploy/prod/backup.sh and restore.sh call it; nothing else does.
"""
import io
import json
import sys
import tarfile
from app.core.config import settings
from app.services.storage_service import storage_service

TYPES = ".syruss-content-types.json"  # object name -> content type, kept beside the files in the archive


def dump(out) -> int:
    client, bucket = storage_service.client, settings.MINIO_BUCKET_NAME
    types = {}
    count = 0
    with tarfile.open(fileobj=out, mode="w|") as tar:
        for obj in client.list_objects(bucket, recursive=True):
            response = client.get_object(bucket, obj.object_name)
            try:
                data = response.read()
                types[obj.object_name] = response.headers.get("Content-Type", "application/octet-stream")
            finally:
                response.close()
                response.release_conn()
            info = tarfile.TarInfo(obj.object_name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            count += 1
        listing = json.dumps(types).encode()
        info = tarfile.TarInfo(TYPES)
        info.size = len(listing)
        tar.addfile(info, io.BytesIO(listing))
    return count


def restore(source) -> int:
    storage_service.ensure_bucket_exists()
    files, types = {}, {}
    with tarfile.open(fileobj=source, mode="r|") as tar:
        for member in tar:
            if not member.isfile() or member.name.startswith("/") or ".." in member.name.split("/"):
                continue  # only plain files with plain names: an archive can't write outside the bucket's own names
            data = tar.extractfile(member).read()
            if member.name == TYPES:
                types = json.loads(data)
            else:
                files[member.name] = data
    for name, data in files.items():
        storage_service.upload_file(object_name=name, data=data, content_type=types.get(name, "application/octet-stream"))
    return len(files)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "dump":
        n = dump(sys.stdout.buffer)
    elif what == "restore":
        n = restore(sys.stdin.buffer)
    else:
        sys.exit("usage: python -m app.backup_files dump|restore")
    print(f"{what}: {n} files", file=sys.stderr)
