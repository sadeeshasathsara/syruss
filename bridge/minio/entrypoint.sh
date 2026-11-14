#!/bin/sh
# Runs MinIO as the minio user. Started as root, it first gives that user whatever in /data it doesn't own yet.
set -eu
if [ "$(id -u)" = "0" ]; then
  find /data ! -user minio -exec chown minio:minio {} + 2>/dev/null || true
  exec su-exec minio:minio minio "$@"
fi
exec minio "$@"
