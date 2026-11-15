---
server: minor
admin: minor
www: minor
agent: minor
---

- **Live support in the same chat.** When someone needs a person now, the assistant asks the Syruss team to join their chat (`request_live_support`).
  - **Only when someone on the staff is online.** Otherwise the assistant offers a ticket.
  - **For the customer:**
    - while waiting, the chat says it is connecting them;
    - once someone accepts, it says "Ana from the Syruss team joined" and that she can see the conversation;
    - both sides see each other typing;
    - the customer can end the chat, and rate it afterwards with a thumbs up or down.
  - **The assistant stays quiet while the team member is in the chat:** messages don't wake it, and a reply it was about to send is refused. When the chat ends, it answers again, with the whole exchange in its history.
  - **Fallbacks:** a chat nobody joins within 10 minutes goes back to the assistant, which offers a ticket. A chat whose team member goes offline goes back to the queue.
- **Admin app: Support → Live chats.**
  - **Your status:** online, away or offline, kept by a heartbeat from any page.
  - **The queue:** each chat shows the assistant's handover note (the conversation itself opens only after you accept), with a sound when a chat starts waiting. The first to accept takes the chat.
  - **Your chats:**
    - the conversation and the customer's details;
    - saved replies (type `/`);
    - hand a chat to someone else who is online;
    - turn it into a ticket for follow-up;
    - end it.
  - Accepting and ending a chat show in the organization's activity and the platform audit log.
  - The Support badge counts chats waiting for someone.
- **The ticket and live chat pages share one customer panel.**
