# Keycloak in production mode, built once by CI so servers only pull it.
# `start-dev` repeats a heavy build on every start (about 5 minutes on a small server); `kc.sh build` does it here.
# Needs at runtime: KC_DB_URL, KC_DB_USERNAME, KC_DB_PASSWORD, KC_HOSTNAME, KC_BOOTSTRAP_ADMIN_*, SYRUSS_* (see deploy/test).
FROM quay.io/keycloak/keycloak:26.7@sha256:37dbaf6f0722c9ec246335f36e1ef8b2e6cb960f7c27e0d8c615121a3d475a85 AS builder

# Build-time options: they must match what the server runs with.
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true \
    KC_METRICS_ENABLED=false

COPY themes/syruss /opt/keycloak/themes/syruss
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.7@sha256:37dbaf6f0722c9ec246335f36e1ef8b2e6cb960f7c27e0d8c615121a3d475a85
COPY --from=builder /opt/keycloak/ /opt/keycloak/
# Substituted from SYRUSS_* environment variables when the realm is imported
COPY realm-syruss.json /opt/keycloak/data/import/realm-syruss.json

ENV KC_HTTP_ENABLED=true \
    KC_PROXY_HEADERS=xforwarded \
    KC_HOSTNAME_STRICT=false

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized", "--import-realm"]
