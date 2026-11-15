---
mail: minor
server: minor
admin: minor
---

- **Each part of Syruss that uses mail can be given its own job.** The Email › Jobs page has a new "Where mail is used" list: invitations and notices, outbound email destinations, the help desk and document inbound, each with a dropdown to choose which job it goes through (only jobs that can do what it does are offered; until you choose, it uses its default). So a job you add can now be put to work: for example a job of your own for email destinations, with its own mailbox.
- **Outbound email destinations now name their own function** instead of always going out as a notification.
- **Document inbound and the help desk follow the job you choose:** channel addresses are made from the chosen document job's mailbox, and mail read from the chosen help job becomes tickets.
- **Developer wording is out of the Email pages** (sealing, fingerprints, where the setup lives); a saved Google or Microsoft app shows as hidden dots and can only be replaced.
- New parts of the server declare their function in one place (`services/mail_uses.py`, with the steps in its header) and appear in the admin app on their own.
