---
server: patch
---

- Workspace settings routes are split into one module per area (general, roles, members, invitations, API keys, webhooks); nothing about the API changes
- Tokens and keys are hashed by one shared helper instead of several copies
