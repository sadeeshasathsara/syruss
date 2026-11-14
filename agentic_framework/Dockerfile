# ==============================================================================
# Syruss AI Agent Worker Dockerfile
#
# Two stages: Python packages are built with compilers in the first, and only the result is copied into a small
# runtime image. The worker runs as an unprivileged user and writes only to /tmp and its home (both tmpfs in
# deploy/*/docker-compose.yml), so the container can run with a read-only filesystem.
# ==============================================================================
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt


FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 syruss \
    && useradd --system --uid 10001 --gid syruss --home-dir /home/syruss --create-home --shell /usr/sbin/nologin syruss

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
# Application source, owned by root: the worker can read its code but not change it
COPY . agentic_framework/

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

USER syruss:syruss

CMD ["python", "-m", "agentic_framework"]
