# Byggs från repots rot: docker build -f core/Dockerfile .
# Alla basavbilder finns för både linux/amd64 och linux/arm64.

# --- Webbgränssnittet (TypeScript) byggs till statiska filer ---
# Resultatet är statiska filer, så steget körs alltid på byggdatorns egen arkitektur.
FROM --platform=$BUILDPLATFORM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Pythonberoenden installeras med uv enligt låsfilen ---
FROM python:3.14-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY core/pyproject.toml core/uv.lock core/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY core/src ./src
RUN uv sync --frozen --no-dev --no-editable

# --- Körbar avbild utan byggverktyg ---
FROM python:3.14-slim
RUN useradd --system --uid 10001 --no-create-home homecontrol
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY --from=web /web/dist /app/web
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOMECONTROL_HOST=0.0.0.0 \
    HOMECONTROL_PORT=8000 \
    HOMECONTROL_WEB_DIR=/app/web
USER homecontrol
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-m", "homecontrol.healthcheck"]
CMD ["python", "-m", "homecontrol"]
