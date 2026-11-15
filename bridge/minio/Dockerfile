# Object storage, built by CI so servers pull it from ghcr instead of Docker Hub.
#
# MinIO runs as the unprivileged minio user (uid 100, group 101); the compose files start it as that user, never root.
# /data belongs to it in the image, so a new volume does too. Started as root anyway (an older setup, or a volume made by
# an older image that root owns), the entrypoint hands /data to minio and drops to it with su-exec before MinIO starts.
FROM alpine/minio:latest-release@sha256:cf23643a6cf9ce159c57643ceb88279e431262282428c9e0bf3a7ef1a97e84b4

USER root
RUN apk upgrade --no-cache && apk add --no-cache su-exec
COPY entrypoint.sh /usr/local/bin/syruss-minio
RUN chmod 0755 /usr/local/bin/syruss-minio
RUN mkdir -p /data && chown minio:minio /data
USER minio

ENTRYPOINT ["/usr/local/bin/syruss-minio"]
CMD ["server", "/data", "--console-address", ":9001"]
