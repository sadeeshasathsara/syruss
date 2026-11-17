---
agent: patch
---

- Fix multi-key rotation: rotate on 403/PERMISSION_DENIED (billing not enabled), raise `AllKeysExhaustedError` when pool is fully exhausted, and eliminate double-sleep in `loop.py` when the pool already handled cooldowns internally
