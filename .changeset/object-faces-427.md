---
agent: minor
server: minor
www: minor
admin: minor
---

- **Every object of a template has its own page, and so does every thing it has met.**
  - **An object's page:** its icon, its attributes (type, required, identity, what each points to), how many documents have it, documents per week, and each attribute in figures (how often it's filled; totals and averages for numbers; first and last for dates; the commonest values otherwise). It also lists what it has met so far (searchable), its documents, the objects connected to it, and the Analytics screens built on it.
  - **A thing's page** (one identified instance, such as one place or one person): its attributes as last read and other ways they were read, first and last seen, mentions per week, what it appears with, what the workspace's knowledge says about it (aliases), and its documents.
  - **Where to find them:** a new **Objects** section in the workspace sidebar (each object is a sub-tab). The template page links each object to its page, and names in Knowledge open their thing's page.
- **Every object has an icon.** The agent picks it from a fixed list of about 180 icons by what the object is and does. The icon shows in template design, on the template, on the profiles and in search. Click an icon in template design to pick another; the list can be searched by meaning ("vehicle", "money"). The platform refuses a name that isn't in the list, and an object the agent leaves without one keeps the icon it has. Objects from before show a general icon until one is chosen.
- **Search finds objects and things**, in the customer app and the admin app. Admin results carry the organization and the workspace as tags.
- **Staff can open the same pages, read-only,** from search or from an organization's workspaces ("Objects"), with the organization and workspace shown.
- **Behind it:** a new table, `record_objects`, which records what each committed record contains (migration 0039). Records are added as they are committed and removed when sent back to review. The scheduler fills it for records saved before, and refills a workspace when its template changes.
