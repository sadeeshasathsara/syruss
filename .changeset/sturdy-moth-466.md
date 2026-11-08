---
server: patch
www: patch
---

- Fixes from code scanning: the CI workflow's token is read-only; an API source that can't be reached says so in a plain sentence (the library's error goes to the server log); the development reset no longer returns raw error text; reading times in rules can't be slowed down by a long run of spaces.
- Connecting a destination through a provider's sign-in now comes back to the destination's page in the main app (PUBLIC_APP_URL), not to the public site.
- An SFTP channel's page shows where to connect (host, port, username) and its password: once, right after the channel is made (kept only in the tab's memory, no longer in browser storage), and again whenever a new one is set.
