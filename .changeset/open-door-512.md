---
server: minor
admin: minor
www: minor
agent: minor
---

- **Support tickets.** Customers can ask the Syruss team for help, and staff answer from the admin app.
  - **Three ways in:**
    - the assistant in a workspace's chat opens one when it can't solve the problem itself;
    - the new Settings → Help & support page;
    - email to the support address.
  - **Who sees a request:** the person who made it, plus the organization's owners and admins.
  - **By email, both ways:**
    - every answer is emailed from the support address, with the ticket number in the subject;
    - replies to it are read from the support mailbox (`SUPPORT_IMAP_*`) and added to the ticket;
    - automatic replies, mail loops and floods from one sender are ignored;
    - mail from an unknown address becomes an "unverified" ticket that gets no email back.
- **Admin app: a new Support section.**
  - **The inbox:** late tickets first, then the most urgent.
  - **Each ticket:** the conversation, internal notes, files, and who the customer is (organization, plan, bills, other requests).
  - **Handling:** assign, change priority or status, merge duplicates.
  - **Saved replies:** pick one from a menu, or type `/` and its shortcut.
  - **Service targets:** hours to the first answer and to a solution, by priority. Unanswered tickets are marked late and the team is told; tickets waiting on the customer are solved and later closed automatically.
  - **Who answers:** the new `support.respond` permission, given to the support and billing roles.
