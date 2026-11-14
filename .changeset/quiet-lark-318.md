---
server: patch
---

- Fixes from code scanning: naming documents in messages can't be slowed down by a long run of dots or dashes (file names are matched up to 200 characters), and a knowledge source that isn't valid JSON is told so in a plain sentence without the parser's own error text.
