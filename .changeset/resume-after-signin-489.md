---
admin: minor
www: minor
---

- **What you were filling in is still there after you sign in again.** When a session ends, or an action asks for a fresh sign-in, and you come back to the page, the dialog you had open and what you had typed are restored: in the admin app the Email dialogs, staff policy and removal, billing (plan changes, add-ons, credits, limits, trials, new invoices) and platform notices and maintenance; in the customer app the new-channel, new-key, channel rules, API key, webhook, role, member removal, destination setup and the confirm-by-typing dialogs for closing an organization, deleting a workspace and deleting an account.
- **The browser is never trusted with it.** The draft is sealed by the app's own server (AES-256-GCM) before it is kept, so the browser holds only an opaque blob it can't read or change. The blob is tied to the person who was signed in, to that page, and to the next 15 minutes; it is used once, and a different person signing in, another page or a changed blob gets nothing. It is kept in the tab only (not in local storage).
- **Passwords, keys, secrets and tokens are never kept**, in the browser or on the server: forms leave them out, and the server drops anything that looks like one even if a form sends it. You retype those.
