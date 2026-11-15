# Help: connecting other systems

How documents come in from other systems and where approved data goes out. All of this is per workspace, under
**Connections** in the workspace sidebar, which only roles with "Manage API keys and webhooks" see. Use the page and
button names exactly as written here. Never paste or repeat a key or secret, and never guess an address or setting the
page doesn't show.

## Connections → Overview
Documents coming in, records going out, and anything held back along the way. **Needs attention** shows what was
refused, paused or couldn't be sent; **Held back** lists approved records not sent yet because something about them is
still open (they go out by themselves once it is settled).

## Connections → Receive: getting documents in
A **channel** is one way documents come in, with its own keys, rules and activity. **New channel** → name it → choose
how documents arrive:
- **API**: another system sends files to a web address with a key (examples in curl, Python and Node on the page).
- **Email**: people or systems email documents to an address of its own.
- **SFTP**: scanners and older systems drop files on a server.
Email and SFTP are available only when switched on for the platform; the page says when they aren't.
Each channel can narrow the file types and the largest file it accepts (never more than the workspace accepts), and
choose whether documents start being read on arrival. A key can be replaced with one that has the same limits, keeping
the old one working for a while (not at all, 1 hour, 24 hours or 7 days) so the sending system can switch without a gap;
revoking a key stops it at once.

## Connections → Publish: sending approved data out
Where approved data goes: your own systems, accounting and ERP. **Nothing is sent until a document is approved and
nothing about it is in doubt.** **Connect a system** offers: Webhook, REST API, Email, S3-compatible storage,
QuickBooks Online, Xero, Odoo, Dynamics 365 Business Central, Oracle NetSuite, SAP S/4HANA. For each you choose what
the other system receives, can try it on an approved document (**Document to try**) to see exactly what is sent, and
set **Send corrections** (send the new version when a record is corrected after it was sent), **Include test
documents**, and **Tries before giving up**. Webhook requests carry a signature made with a signing secret so the
receiving system can tell they came from Syruss.

## Connections → Deliveries
Every approved record on its way out: where it is, why it is waiting, and what happened on each try (the request and
reply are shown). Filter by destination or search.

## Connections → Developers
A quickstart (three steps from nothing to a document arriving) and what each API answer means, for example: the key is
missing, wrong, expired or revoked; the plan's allowance is used up; the file is too big or not an accepted type (what
the file really is counts, not its name); too many requests (Retry-After says when to try again).

## Settings → Integrations
- **API keys**: **Create a key**. Read keys see everything; write keys can also upload and review. Sent as
  Authorization: Bearer with the workspace's X-Template-ID.
- **Webhooks**: **Add an endpoint**; each POST is signed (X-Webhook-Signature, X-Webhook-Timestamp) with your secret.

## Problems
A delivery that keeps failing: open it in Deliveries and read the reply from the other system; the cause is usually
there (a changed password or address on their side). If it is not clear, this needs Syruss support.
