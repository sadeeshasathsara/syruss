# Syruss — First-Time Developer Setup Guide

This guide walks you through setting up the complete Syruss platform on your local machine for the first time.

---

## 1. System Architecture & Monorepo Overview

Syruss is architected as an event-driven, multi-tenant document intelligence platform composed of the following services:

```
document-processing-agentic-system/
├── bridge/             # Infrastructure (Docker Compose: Postgres, MinIO, Kafka, Keycloak, Mailpit)
├── server/             # Core FastAPI backend (Business logic, REST API, WebSockets, DB migrations) :8000
├── agentic_framework/  # Autonomous Python AI agent worker (Kafka consumer, LLM tool execution)
├── site/               # Next.js landing page, onboarding, invitation flows :3100
├── www/                # Next.js main workspace application :3000
└── admin/              # Next.js system administration dashboard :3200
```

### High-Level Communications Flow

```mermaid
flowchart LR
    Browser([Browser]) <-->|Port 3100 / 3000 / 3200| Frontends[Next.js Apps]
    Frontends <-->|/api/v1 (Bearer JWT)| Core[FastAPI Core Server :8000]
    Frontends <-->|OIDC PKCE| KC[(Keycloak :8080)]
    Core -->|Outbox -> agent.events| K[(Kafka :9092)]
    K --> Agent[Agentic Framework Worker]
    Agent -->|/internal/agent/v1| Core
    Agent -->|agent.telemetry| K
    K -->|WebSocket / SSE Bridge| Frontends
    Core --- DB[(PostgreSQL :5432)]
    Core --- S3[(MinIO Object Store :9000)]
    Core -->|SMTP| Mail[(Mailpit :1025 / UI :8025)]
```

---

## 2. Prerequisites

Before starting, ensure you have the following installed:

| Tool | Minimum Version | Verification Command | Notes |
|---|---|---|---|
| **Git** | 2.40+ | `git --version` | Configured with SSH or HTTPS |
| **Node.js** | 22.0.0+ | `node --version` | Required by modern packages (e.g., `nanoid@6`) |
| **npm** | 10.9.0+ | `npm --version` | Bundled with Node 22 |
| **Python** | 3.12.0+ | `python --version` | Standard Python 3.12 distribution |
| **Docker** | 24.0+ | `docker --version` | Docker Desktop with Linux containers enabled |
| **Docker Compose** | v2.20+ | `docker compose version` | Bundled with modern Docker |

---

## 3. Git Workflow & Branch Strategy

We adhere strictly to a 3-tier branch hierarchy:

```
main  (Production releases — vX.Y.Z)
  ↑ (PR only after QA staging)
test  (Release candidates / Staging — vX.Y.Z-rc.N)
  ↑ (PR only from dev)
dev   (Active development — vX.Y.Z-dev.N)
```

### Git Rules

1. **Always work on `dev`** or a feature branch branched off `dev`.
2. **Conventional Commits**: Every commit or pull request must follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:
   - `feat(scope): ...` triggers **Minor** version bump (e.g. `1.1.0` -> `1.2.0`)
   - `fix(scope): ...` triggers **Patch** version bump (e.g. `1.2.0` -> `1.2.1`)
   - `feat(scope)!: ...` triggers **Major** version bump (e.g. `1.2.1` -> `2.0.0`)
   - `chore:`, `docs:`, `test:`, `refactor:`, `ci:` produce **no version bump**
3. Pull requests to `dev`, `test`, and `main` have an automated GitHub Action check that enforces valid commit message formatting.

---

## 4. Step-by-Step Setup Instructions

### Step 1: Clone the Repository

```bash
git clone https://github.com/sadeeshasathsara/syruss.git
cd syruss
git checkout dev
```

---

### Step 2: Start Infrastructure with Docker Compose

All backing services (Postgres, MinIO, Kafka, Mailpit, and Keycloak) are orchestrated via `bridge/docker-compose.yml`.

1. Copy the bridge environment configuration:
   ```bash
   cp bridge/.env.example bridge/.env
   ```
2. Start the infrastructure stack in the background:
   ```bash
   cd bridge
   docker compose up -d
   cd ..
   ```
3. Verify that all containers are healthy:
   ```bash
   docker compose -f bridge/docker-compose.yml ps
   ```

#### Backing Service Endpoints:
- **PostgreSQL**: `localhost:5432` (`user: postgres`, `password: change-me`, `database: doc_intelligence`)
- **MinIO S3 Console**: `http://localhost:9001` (API: `localhost:9000`, `admin / change-me`)
- **Keycloak Admin**: `http://localhost:8080` (`admin / change-me`)
- **Mailpit Web UI**: `http://localhost:8025` (SMTP: `localhost:1025`)
- **Kafka Broker**: `localhost:9092`

> **Note on Keycloak**: The `syruss` realm is automatically imported from `bridge/keycloak/realm-syruss.json` on initial boot.

---

### Step 3: Python Environment & Core Backend (`server/`)

1. Create and activate a root Python 3.12 virtual environment:
   ```bash
   # Windows (PowerShell)
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

   # macOS / Linux
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```

2. Upgrade pip and install server dependencies:
   ```bash
   pip install --upgrade pip
   pip install -r server/requirements.txt
   ```

3. Setup environment variables for the server:
   ```bash
   # Windows (PowerShell)
   Copy-Item server/.env.example server/.env

   # macOS / Linux
   cp server/.env.example server/.env
   ```

4. Initialize database migrations:
   ```bash
   cd server
   alembic upgrade head
   cd ..
   ```

5. Run the FastAPI development server:
   ```bash
   cd server
   uvicorn app.main:app --reload --port 8000
   ```
   - OpenAPI Swagger Docs: [http://localhost:8000/docs](http://localhost:8000/docs)
   - Health Check: [http://localhost:8000/health](http://localhost:8000/health)

---

### Step 4: AI Agent Worker (`agentic_framework/`)

The agent worker listens to Kafka events from the core server and executes agentic document processing and extraction tools.

1. Install agent dependencies (in the same or separate virtualenv):
   ```bash
   pip install -r agentic_framework/requirements.txt
   ```

2. Setup agent environment variables:
   ```bash
   # Windows (PowerShell)
   Copy-Item agentic_framework/.env.example agentic_framework/.env

   # macOS / Linux
   cp agentic_framework/.env.example agentic_framework/.env
   ```

3. Configure your LLM Provider in `agentic_framework/.env`:
   ```env
   LLM_PROVIDER=gemini        # gemini | anthropic | openai
   GEMINI_API_KEY=your_key_here
   GEMINI_MODEL=gemini-2.5-flash
   ```

4. Run the agent worker process:
   ```bash
   python -m agentic_framework
   ```

---

### Step 5: Landing & Onboarding App (`site/`)

1. Install Node.js dependencies:
   ```bash
   cd site
   npm install
   ```

2. Configure local environment variables:
   ```bash
   # Windows (PowerShell)
   Copy-Item .env.example .env.local

   # macOS / Linux
   cp .env.example .env.local
   ```

3. Generate a 32-character session secret:
   ```bash
   node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"
   ```
   Paste the generated string into `site/.env.local` as `SESSION_SECRET=...`.

4. Start the development server (runs on port 3100):
   ```bash
   npm run dev
   ```
   Access at: [http://localhost:3100](http://localhost:3100)

---

### Step 6: Main Workspace Application (`www/`)

1. Install Node.js dependencies:
   ```bash
   cd ../www
   npm install
   ```

2. Configure local environment variables:
   ```bash
   # Windows (PowerShell)
   Copy-Item .env.example .env.local

   # macOS / Linux
   cp .env.example .env.local
   ```

3. Generate a session secret (or use the one from `site/`):
   ```bash
   node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"
   ```
   Paste the generated string into `www/.env.local` as `SESSION_SECRET=...`.

4. Start the development server (runs on port 3000):
   ```bash
   npm run dev
   ```
   Access at: [http://localhost:3000](http://localhost:3000)

---

## 5. Running the Test Suite

### Backend Unit & Integration Tests (pytest)

Run the full pytest suite against the running PostgreSQL & MinIO instance:

```bash
cd server
pytest tests/
```
All **268+ tests** (including vendor ERP mock connectors: SAP, QuickBooks, Xero, NetSuite, Dynamics, Odoo) should execute and pass cleanly.

### Frontend Compilation & Typecheck

Verify TypeScript compilation and Next.js builds:

```bash
# www app
cd www
npx tsc --noEmit
npm run build

# site app
cd ../site
npx tsc --noEmit
npm run build
```

---

## 6. Common Gotchas & Troubleshooting

### 1. `cache lookup failed for type ...` in asyncpg
- **Cause**: Recreating tables/enums in PostgreSQL during test runs changes internal OIDs while prepared connection pools retain old mappings.
- **Fix**: The backend automatically invokes `await engine.dispose()` in `conftest.py` after schema reset. If running manual resets, restart the FastAPI server.

### 2. Node Engine Warning (`nanoid@6 requires node >= 22`)
- Ensure you are running Node.js version 22 or higher (`node -v`). Using Node 20 will cause `EBADENGINE` warnings and lockfile synchronization failures during clean installs.

### 3. Port Conflicts
Ensure none of the default service ports are already bound by host processes:
- `3000` (www)
- `3100` (site)
- `3200` (admin)
- `5432` (PostgreSQL)
- `8000` (FastAPI)
- `8025` / `1025` (Mailpit)
- `8080` (Keycloak)
- `9000` / `9001` (MinIO)
- `9092` (Kafka)

### 4. Email & SFTP Intake Channels Disabled in UI
- In `server/.env`, `INTAKE_SMTP_PORT=0` and `INTAKE_SFTP_PORT=0` disable email and SFTP ingestion by default in local development.
- To enable local SFTP and email receivers, assign non-zero ports (e.g. `INTAKE_SMTP_PORT=2525`, `INTAKE_SFTP_PORT=2222`).

---

## 7. Containerization & CI/CD

Each service includes a production-ready multi-stage Dockerfile:
- `server/Dockerfile`
- `agentic_framework/Dockerfile`
- `site/Dockerfile` (Node 22 Next.js standalone)
- `www/Dockerfile` (Node 22 Next.js standalone)

GitHub Actions workflows automatically build and publish versioned container images to the GitHub Container Registry (`ghcr.io/sadeeshasathsara/...`) whenever commits are pushed to `dev`, `test`, or `main`.
