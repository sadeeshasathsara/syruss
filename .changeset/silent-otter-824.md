---
server: minor
---

- Plans belong to the organization: one subscription per organization, and its workspaces share the plan's monthly page allowance. Workspace limits (members, API keys, webhooks, channels, destinations) still apply to each workspace.
- Every document accepted for reading is charged its pages (a multi-page TIFF counts each page); test-key documents, samples and repeats cost nothing. When the period's pages are used, new documents are refused with when they renew.
- The plans are now a versioned catalog in the database (plans and add-ons), and every limit a plan can set is registered in code, so only limits the platform enforces can be sold. Overrides set a limit by hand for an organization or one workspace.
- Existing organizations move onto the highest plan any of their workspaces had, and keep any extra monthly capacity their workspaces had together.
