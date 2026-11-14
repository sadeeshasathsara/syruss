---
agent: minor
server: minor
www: patch
---

- **Defenses against prompt injection, in four layers:**
  - **Data is marked as data.** Every tool result reaches the model between markers carrying a random value per run, which a document can't fake or close. Every charter says nothing inside them is an instruction, and that such text is described, never passed on word for word (no images, links or code copied from data).
  - **What the agent never shares:** its instructions and setup, anything about other workspaces, other people's chats or Syruss staff, secrets, internal IDs. It doesn't assess the platform's security, list its weak points, or help probe it, whoever asks: security questions go to Syruss support.
  - **A server-side guard on everything the agent says to people** (chat replies, flags, the conversations it opens and closes):
    - a reply that repeats its instructions is refused;
    - keys, tokens, private keys, connection strings, internal IDs and images are cut out;
    - each catch is written to the workspace's activity, as the kinds removed, never the text.
  - **The chat never loads images from agent text** (the description is shown instead), and links to other sites show which site.
- **Injection tests:**
  - `python -m agentic_framework.redteam` attacks the real model with seven cases: asking for its instructions, claiming authority, probing security, asking for other people's data, and documents that try to command it, smuggle data out through an image, or carry a secret.
  - It runs from `.github/workflows/agent-redteam.yml` on pull requests that change the agent, weekly and by hand, once the `GEMINI_API_KEY` secret is set.
