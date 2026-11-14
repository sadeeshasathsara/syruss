---
server: patch
agent: patch
www: patch
site: patch
admin: patch
---

- **Containers are hardened.**
  - The server and the agent are now two-stage images with no compilers or curl, and run as an unprivileged user.
  - MinIO drops to its own user after taking ownership of its data.
  - Every base image is pinned by digest, and the web apps install exactly what their lockfiles list.
- **In the production and test compose files:**
  - No container keeps Linux capabilities except the few the database images need to switch user, and none can gain privileges.
  - Process counts are limited.
  - Syruss's own services run with read-only filesystems.
  - The network is split so the web apps can't reach the databases, and the databases, Kafka and object storage have no internet access.
- **Scanning and updates:**
  - A container scan (Trivy) runs on pull requests that change images and every week, and fails on critical fixable vulnerabilities in Syruss's own images.
  - Dependabot keeps the pinned base images current.
- The web apps' lockfiles had fallen out of step with their package.json (the old `npm ci || npm install` fallback hid it in builds and in CI). They are regenerated with the build image's npm (only where `picomatch` sits changed), and installs are strict everywhere now.
- **Security fixes found by the new scan:**
  - Next.js 16.3.7 → 16.4.6 in all three web apps (two critical remote-code-execution flaws, GHSA-2464-fhr4-w9xw and GHSA-673q-mjm6-r5xr).
  - `follow-redirects` and `source-map-js` updated in www.
  - The running images no longer carry package managers (npm, yarn, corepack, pip) or the flaws bundled with them.
  - Keycloak 26.0 → 26.7 (an unauthenticated flaw in keycloak-services, plus Netty, FreeMarker and BouncyCastle).
  - Kafka 3.7 → 3.9.2 (Netty, libexpat).
- **Sign-in:**
  - Keycloak's account page works again: its frame headers allow its own pages (SAMEORIGIN) instead of none, which had broken setting up two-step sign-in.
  - Customers can tick "Keep me signed in" for 30 days, so their password and one-time code aren't asked again until then. Staff still sign in with a code each time.
- **The local development stack** (`bridge/docker-compose.yml`):
  - Answers on this machine only.
  - Drops Linux capabilities, runs MinIO as its own user, and pins every image.
