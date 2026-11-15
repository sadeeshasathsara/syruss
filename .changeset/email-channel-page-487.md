---
www: minor
---

- **An email channel's page is about email, not API keys.** It shows the channel's address with a copy button and a short note that anyone can send documents with an ordinary email (a person from their inbox, or a system that mails reports), with no key or sign-in. The "Keys" section, the API request example and the key dialog now appear only on API channels, so the "Keys are for API channels" error can no longer be reached from an email or SFTP channel.
- **Who can email documents is now set on the page:** a list of allowed addresses or @domains (empty: anyone with the address), and an option to accept only mail that passes the sender's SPF or DKIM check. Both existed on the server but had no place in the app.
