# Object storage, built by CI so servers pull it from ghcr instead of Docker Hub.
FROM alpine/minio:latest-release

USER 0:0
CMD ["server", "/data", "--console-address", ":9001"]
