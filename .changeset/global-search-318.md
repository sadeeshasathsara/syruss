---
server: minor
admin: minor
www: minor
---

- **One search for everything, in both apps.** Open it from the top bar, with Ctrl+K (⌘K on a Mac) or with `/`.
  - **As you type:** pages to go to match at once; everything else is asked of the server and comes back in groups, exact and prefix matches first, with the matched words marked.
  - **Keyboard:** arrow keys move, Enter opens, Ctrl/⌘+Enter opens in a new tab, Esc closes.
  - **Remembered in this browser:** your recent searches and what you opened, each with a Clear.
  - **On phones** it fills the screen, with a search button in the top bar.
- **Admin app:** finds organizations, people, documents (file name, the sender's reference or the document's key), agent runs, support tickets (number, subject or requester), invoices, workspaces and staff. Any id finds its thing. Every page in the navigation you may open is a place to go to.
- **Customer app (new):** finds, in the organization you're in, only what you may open:
  - workspaces you belong to (owners and admins also find the others);
  - documents, only in workspaces where your role can view documents;
  - people in the organization;
  - your own chats, which open in their workspace's chat panel;
  - your support requests (all of the organization's for owners and admins);
  - invoices, only for people who manage billing.

  The organization's pages and every settings page are places to go to.
- **Server:** `GET /api/v1/search` for the customer app; the staff search (`/api/v1/sysadmin/search`) answers in the same grouped shape.
