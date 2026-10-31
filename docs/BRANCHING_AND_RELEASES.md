# Syruss — Git Branching, Versioning & Governance Guide

This document defines the production engineering standards for the Syruss monorepo.

---

## 1. Branching Strategy

The repository maintains three persistent long-lived branches in a strict promotion hierarchy:

```
                  ┌───────────────┐
                  │     main      │  (Production: stable releases, tagged vX.Y.Z)
                  └───────┬───────┘
                          │ (branched from main)
                          ▼
                  ┌───────────────┐
                  │     test      │  (Staging / QA: release candidates, tagged vX.Y.Z-rc.N)
                  └───────┬───────┘
                          │ (branched from test)
                          ▼
                  ┌───────────────┐
                  │      dev      │  (Development: active integration, tagged vX.Y.Z-dev.N)
                  └───────┬───────┘
                          │ (branched from dev)
                          ▼
              ┌───────────────────────┐
              │  feat/*   or   fix/*  │  (Feature / bugfix working branches)
              └───────────────────────┘
```

### Branch Responsibilities

| Branch | Environment | Version Format | Access & Merge Rules |
|---|---|---|---|
| **`main`** | **Production** | `vX.Y.Z` (e.g. `v1.2.0`) | Protected. PR only from `test`. Requires 2 approvals + passing CI. |
| **`test`** | **Staging / QA** | `vX.Y.Z-rc.N` (e.g. `v1.2.0-rc.1`) | Protected. PR only from `dev`. Requires 1 approval + full integration tests. |
| **`dev`** | **Development** | `vX.Y.Z-dev.N` (e.g. `v1.2.0-dev.4`) | Protected. PR from feature branches. Requires 1 approval + unit/lint CI. |
| **`feat/*`** / **`fix/*`** | Local / Sandbox | N/A | Developer branches. Rebased onto `dev` before PR. |

---

## 2. Commit Message Standard (Conventional Commits)

You write the **exact same standard commit messages** regardless of what branch you are on. The CI pipeline inspects these messages and determines the version bump automatically for that branch.

### Format
```
<type>(<scope>): <short description>

[optional body]

[optional footer(s)]
```

### Allowed Types & Version Bump Rules

| Type | Intent | SemVer Impact | Example |
|---|---|---|---|
| **`feat`** | A new feature | **Minor** (`1.1.0` -> `1.2.0`) | `feat(connections): add sap s4hana connector` |
| **`fix`** | A bug fix | **Patch** (`1.2.0` -> `1.2.1`) | `fix(sftp): handle stale asyncpg connection pool` |
| **`feat!`** or **`fix!`** | **BREAKING CHANGE** | **Major** (`1.2.1` -> `2.0.0`) | `feat(auth)!: replace api key headers with bearer tokens` |
| **`test`** | Adding or fixing tests | No version bump | `test(intake): add presigned upload expiry test` |
| **`docs`** | Documentation only | No version bump | `docs(readme): add docker compose startup instructions` |
| **`chore`** | Maintenance / dependencies | No version bump | `chore(deps): update httpx to 0.28.0` |
| **`refactor`** | Code change that neither fixes nor adds | No version bump | `refactor(deliveries): simplify backoff calculation` |
| **`perf`** | Performance improvement | **Patch** (or no bump) | `perf(mapping): cache compiled jmespath expressions` |

### Common Scopes
- `intake`: Intake channels, API keys, email receiver, SFTP
- `connections`: Destinations, webhook sender, ERP connectors (QBO, Xero, NetSuite, SAP, etc.)
- `agent`: LangGraph agent, memory, prompts, tools
- `ui`: Next.js frontend (`www/` or `site/`)
- `auth`: Keycloak, permissions, RBAC, session management
- `db`: Alembic migrations, database models

---

## 3. Automated Versioning Across Branches

You do **not** need different commit formats for each branch. The GitHub Actions workflows automate versioning per branch:

### On `dev` branch:
- Every merge to `dev` calculates the next version and appends a pre-release dev suffix:
  - Example: `v1.2.0-dev.1`, `v1.2.0-dev.2`
- Deploys Docker images tagged `:dev` and `:v1.2.0-dev.N`.

### On `test` branch:
- When a PR from `dev` merges into `test`, CI creates a **Release Candidate** tag:
  - Example: `v1.2.0-rc.1`, `v1.2.0-rc.2`
- Deploys Docker images tagged `:staging` and `:v1.2.0-rc.N`.
- Triggers QA and manual verification.

### On `main` branch:
- When `test` is verified and merged into `main`, CI promotes the release candidate to **Stable**:
  - Example: `v1.2.0`
  - Generates release notes in `CHANGELOG.md`.
  - Creates the official GitHub Release with artifacts.
  - Promotes the tested `:v1.2.0-rc.N` container to `:latest` and `:v1.2.0`.

---

## 4. Promotion Lifecycle (Step-by-Step)

### Day-to-Day Feature Development
1. Developer pulls latest `dev`:
   ```bash
   git checkout dev
   git pull origin dev
   ```
2. Create a feature branch:
   ```bash
   git checkout -b feat/odoo-tax-rounding dev
   ```
3. Commit with Conventional Commits:
   ```bash
   git commit -m "feat(odoo): add automatic tax rounding adjustment line"
   ```
4. Push and open a Pull Request into **`dev`**.
5. CI runs lint, typecheck, and unit tests. On approval, merge to `dev`.
6. CI automatically tags `dev` (e.g. `v1.2.0-dev.14`).

### Releasing to Staging (`test`)
1. When features on `dev` are ready for validation, open a PR from **`dev` -> `test`**.
2. PR title: `chore(release): promote dev to test (v1.2.0-rc.1)`.
3. CI runs full integration suite (all 268+ tests, DB migrations, mock vendors).
4. On merge, CI creates release candidate tag `v1.2.0-rc.1` and deploys to Staging.
5. QA conducts manual tests and browser checks.

### Releasing to Production (`main`)
1. When staging passes all checks, open a PR from **`test` -> `main`**.
2. PR title: `chore(release): release v1.2.0`.
3. Once approved, merge to `main`.
4. CI generates `v1.2.0` tag, updates `CHANGELOG.md`, and triggers production deployment.

### Hotfix Workflow (Emergency Bug in Production)
1. Branch directly from `main`:
   ```bash
   git checkout -b hotfix/expired-token-crash main
   ```
2. Commit fix:
   ```bash
   git commit -m "fix(auth): handle expired refresh token gracefully"
   ```
3. PR into `main` -> merged -> tags `v1.2.1`.
4. **Immediate backport**:
   ```bash
   git checkout test && git merge main
   git checkout dev && git merge test
   ```

---

## 5. Branch Protection Rules Summary

Set these up in **GitHub Repository Settings -> Branches -> Add branch protection rule**:

### Rule for `main`:
- Require a pull request before merging (2 approvals).
- Dismiss stale pull request approvals when new commits are pushed.
- Require status checks to pass before merging (`Validate Pull Request / test-full`).
- Require branches to be up to date before merging.
- Do not allow bypass of the above settings.

### Rule for `test`:
- Require a pull request before merging (1 approval).
- Require status checks to pass before merging (`Validate Pull Request / test-full`).
- Do not allow force pushes or deletions.

### Rule for `dev`:
- Require a pull request before merging (1 approval).
- Require status checks to pass before merging (`Validate Pull Request / test-fast`).
- Do not allow force pushes or deletions.
