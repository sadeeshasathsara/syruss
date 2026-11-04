# Keycloak's own database, kept apart from business data. Built by CI so servers pull it from ghcr.
# Small-memory settings are baked in.
FROM postgres:16-alpine

CMD ["postgres", "-c", "shared_buffers=16MB", "-c", "max_connections=20"]
