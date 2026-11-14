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
