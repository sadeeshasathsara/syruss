# ==============================================================================
# Syruss API Server Dockerfile
#
# Two stages: Python packages are built with compilers in the first, and only the result is copied into a small
# runtime image (no compilers, no package build tools, no curl). The server runs as an unprivileged user and writes
# only to /tmp, so the container can run with a read-only filesystem (see deploy/*/docker-compose.yml).
# ==============================================================================
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt


FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH

# Runtime libraries only: Postgres client library, libmagic for file type checks, DejaVu so invoice PDFs print most scripts
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 libmagic1 fonts-dejavu-core \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 syruss \
    && useradd --system --uid 10001 --gid syruss --home-dir /home/syruss --create-home --shell /usr/sbin/nologin syruss

COPY --from=build /opt/venv /opt/venv
# Nothing installs packages at runtime: no pip, in the image or the environment
RUN /opt/venv/bin/python -m pip uninstall -y -q pip && /usr/local/bin/python -m pip uninstall -y -q pip

WORKDIR /app
# Application source and migrations, owned by root: the server can read its code but not change it
COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini .

# Injected at build time by CI for dynamic version reporting
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

USER syruss:syruss
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status != 200)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
