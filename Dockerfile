# syntax=docker/dockerfile:1.7
# Stage 1: build the React + TypeScript frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
# An optional CA bundle (BuildKit secret "ca") lets the build run behind a TLS-inspecting proxy; it is not kept.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca; fi; npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python 3.12 runtime with the WeasyPrint system libraries.
# Pinned to bookworm so the package names below stay valid.
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Debian over HTTPS; the optional CA secret is only for builds behind a TLS-inspecting proxy.
RUN --mount=type=secret,id=ca,required=false \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && CA=""; if [ -f /run/secrets/ca ]; then CA="-o Acquire::https::CAInfo=/run/secrets/ca"; fi \
    && apt-get $CA update && apt-get $CA install -y --no-install-recommends \
    libpango-1.0-0 libharfbuzz0b libharfbuzz-subset0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libcairo2 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; pip install --no-cache-dir uv==0.11.32
COPY pyproject.toml uv.lock ./
# Dependencies only; the project itself runs from source.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"

COPY backend/ ./backend/
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Verify the replay fixtures at build time so broken data can never be deployed.
RUN python -m scripts.verify_golden_fixtures

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/api/v1/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
