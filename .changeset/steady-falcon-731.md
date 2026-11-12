---
server: patch
www: patch
admin: patch
site: patch
agent: patch
---

- Live updates go only to the channel they were sent to. Every event used to be copied to an organization-wide channel that any member could open, which carried other people's notifications, private chats and workspaces they aren't in. That channel no longer exists.
- Intake key address allowlists read only the last X-Forwarded-For entry (the one your proxy added) when TRUST_PROXY_HEADERS is on, so a sender can't put an allowed address in front of their own.
- Sign-in return addresses can't be turned into links to other sites (a tab or line break in the path used to be dropped by the browser and make one), and the app's API proxies refuse "." and ".." in a path, so a request can't step outside /api/v1 (or outside /sysadmin in the admin app).
- Calls to customers' systems (knowledge sources, destinations, webhooks) check the address again at the moment of connecting, so a name that answers with a public address and then a private one can't reach the private network. Shared (100.64/10) and IPv4-in-IPv6 addresses count as private too, and a webhook's reply body is no longer read.
- Creating a template that starts a new workspace takes the organization's workspaces.create permission, like POST /workspaces. Deleting a workspace through the template route asks for a recent sign-in, like the workspace route.
- A live session id must be the caller's own: another person's chat or a document's id is refused (it could put words in someone's chat or open a document's live feed). Asking the workspace agent from a new help session no longer fails.
- Nobody but an owner can change the role of, or remove, a workspace member whose role has privileges theirs doesn't.
- Files whose names are in other scripts (or contain quotes) download instead of failing; CSV exports (screens, the platform's billing export, the email destination's attachment) write values that a spreadsheet would run as formulas as text; the email destination's subject fills plain {name} placeholders only.
- Request bodies sent without a length are held to MAX_REQUEST_MB as they arrive; a knowledge source file is limited like an API source's reply; SFTP files are limited while being written and links can't be made; document and audit lists take at most 500 and 200 a page; the sign-in rate limiter forgets old addresses; the live replay history keeps only recent channels; email intake sender lists match regardless of case.
- The agent's calculator refuses powers, numbers and lists big enough to stall the worker, and the agent reads a public key given on one line (with \n for its line breaks).
