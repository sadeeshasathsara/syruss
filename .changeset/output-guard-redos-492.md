---
server: patch
---

- **The check on what the agent says can't be slowed down by text made of thousands of image starts.** Removing markdown and HTML images from the agent's replies, flags and conversations is now a single pass over the text instead of one regular expression that could work through the rest of the text from every `![` or `<img`. What is removed is unchanged.
