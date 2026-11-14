# Object storage, built by CI so servers pull it from ghcr instead of Docker Hub.
#
# MinIO itself runs as the unprivileged minio user (uid 100). The entrypoint starts as root only to hand the data
# volume to that user (volumes made by older images belong to root), then drops to it with su-exec before MinIO starts,
# as the Postgres images do. In docker-compose it may do nothing else: every other root power is dropped there.
FROM alpine/minio:latest-release@sha256:cf23643a6cf9ce159c57643ceb88279e431262282428c9e0bf3a7ef1a97e84b4

USER root
RUN apk upgrade --no-cache && apk add --no-cache su-exec
COPY entrypoint.sh /usr/local/bin/syruss-minio
RUN chmod 0755 /usr/local/bin/syruss-minio

ENTRYPOINT ["/usr/local/bin/syruss-minio"]
CMD ["server", "/data", "--console-address", ":9001"]
