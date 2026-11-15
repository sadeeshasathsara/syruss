---
server: patch
---

- **`get_db_session` is typed as what it is**, an async iterator of sessions. Pylance reported it as invalid (`reportInvalidTypeForm`). Behaviour doesn't change.
