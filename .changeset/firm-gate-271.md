---
server: patch
agent: patch
---

- **The test server and production refuse to start with development values, and say which.**
  - **New `ENV` value `test`:** the test server's compose file sets it for the server and the agent. Production's compose file now also sets `ENV=production` for the agent, which it didn't before.
  - **Server:**
    - the development database, storage and token passwords;
    - no `SECRETS_ENCRYPTION_KEY`;
    - `OIDC_ISSUER`, `PUBLIC_SITE_URL`, `PUBLIC_APP_URL`, `PUBLIC_ADMIN_URL`, `PUBLIC_API_URL` or `CORS_ORIGINS` not https, or still localhost;
    - `MAIL_FROM` at a `.local` domain;
    - mail that would only be logged or sent to localhost;
    - no agent signing key.
  - **Agent:** the development database password, or no `AGENT_TOKEN_PUBLIC_KEY`.
  - **Production also refuses** weakened security switches (`REQUIRE_*`), as before.
  - **Typos:** an unknown `ENV` (for example `prod`) stops the start instead of quietly running as development.
