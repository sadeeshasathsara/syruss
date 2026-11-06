---
admin: minor
server: minor
agent: minor
www: patch
---

- The system admin app follows every agent run and every document on the platform, in every organization, as it happens: a Live page (what is running now, what just happened), Agent runs (filters, totals, runs per hour, the busiest organizations), one run step by step with every try and what it changed, Documents with each one's whole journey from arrival to delivery and how long each part took, and Queues (events waiting or never picked up, the agent's Kafka lag, failures, stuck documents)
- The agent saves a run when it starts and after every step, not only at the end, so a crash leaves its steps behind and the platform sees what is running now; a try that fails ends as failed with the steps it got through
- A run that stops reporting for 15 minutes is marked lost and written in its document's log; step records are kept 90 days, run summaries for good
- Log and audit entries the agent writes name the run that wrote them
- Staff see facts only (what ran, where, when, for how long, at what cost); a customer's content (thoughts, tool results, records, messages, the file) opens only through an access grant with a reason and an end time, each opening is in the platform audit log, and the organization sees in its own activity that Syruss support looked and why
- Staff with operations rights can stop a run, send a failed or stuck document to the agent again and send a waiting event again, each with a reason the organization also sees
- The run log in the main app shows running, stopped and lost runs; stopping a chat reply works while its run is under way
