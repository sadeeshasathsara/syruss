# Keycloak's own database, kept apart from business data. Built by CI so servers pull it from ghcr.
# Small-memory settings are baked in.
FROM postgres:16-alpine@sha256:f377d590e5474681d4d7838635031a2b50647923b7abfa803ce9b5ff35d5c663

CMD ["postgres", "-c", "shared_buffers=16MB", "-c", "max_connections=20"]
