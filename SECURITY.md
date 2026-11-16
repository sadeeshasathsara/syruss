# Security policy

## Reporting a vulnerability

**Please don't open a public issue or pull request for a security problem.** Report it privately:

1. Go to https://github.com/sadeeshasathsara/syruss/security/advisories/new (the repository's **Security** tab, then **Report a vulnerability**).
2. Say what you found, where, and how to reproduce it. A short proof of concept, the version or commit, and the impact as you see it are
   the most useful things. Don't include real customers' data.

You will get an answer within **3 working days**, and an update on the fix or the decision at least every **14 days** after that. When the
fix is released we are glad to credit you, if you want to be named.

## Scope

In scope: this repository and what it ships: the API server (`server/`), the agent (`agentic_framework/`), the mail service (`mail/`),
the customer app (`www/`), the admin app (`admin/`) and the site (`site/`), and the deployment files in `deploy/` and `bridge/`.

Out of scope: findings that need a compromised machine or an already-privileged account to matter; missing best-practice headers with no
demonstrable impact; denial of service by sheer traffic volume; social engineering of the maintainers; vulnerabilities in a dependency with
no way to reach it from here (report those upstream).

## Supported versions

Fixes are made on `dev`, released through `test` and then `main`. The latest release on `main` is supported; older releases are not
patched separately.

## What we do in return

- We don't take legal action against research that follows this policy: reporting privately, not reading or keeping other people's data
  beyond what is needed to show the problem, no disruption of a running service, and a reasonable time to fix before any disclosure.
- Findings from our own reviews are written up in `docs/`, and the security-relevant parts of the design are in `README.md` and
  `mail/README.md` (sealed secrets, signed commands, Kafka security).

## For people running Syruss

Set the production secrets, turn on TLS and SASL for Kafka, keep the master keys apart from the database backups, and keep dependencies
current (`.github/dependabot.yml` is set up for this). `deploy/prod/README.md` is the runbook.
