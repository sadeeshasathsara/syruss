---
agent: minor
server: minor
www: minor
---

- Template rules: checks in a safe rule language that run on every record after extraction; failing error rules send the document to review, warnings are reported
- Rules tab under Template with suggested, active, stopped and turned-off rules; new-rule page to describe a rule in plain English (sent to Syruss in a new chat) or write it with a live compile check and dry run
- Syruss can list, try and suggest rules (a person adds them), with a rules guide; rules that no longer fit after a template change are stopped and announced
- Legacy expression and regex checks now run on the safe engine (RE2, capped exponents)
