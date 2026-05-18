# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.6-python3.11-bookworm-slim AS builder

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
ARG HF_TOKEN

ENV UV_HTTP_TIMEOUT=1200
ENV UV_LINK_MODE=copy
ENV HF_HOME=/opt/wisepen/models/huggingface
ENV HF_TOKEN=${HF_TOKEN}
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY uv.lock ./uv.lock

# Root workspace pyproject for Docker build.
# Do not add this file to the repository unless you explicitly want a root uv workspace file.
RUN cat > /app/pyproject.toml <<'EOF'
[project]
name = "wisepen-build-workspace"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = []

[tool.uv]
package = false

[tool.uv.workspace]
members = [
  "services/wisepen-common",
  "services/wisepen-chat-service"
]
EOF

# Copy only package metadata first.
# This keeps the expensive dependency installation layer cached when only .py source files change.
RUN mkdir -p \
    /app/services/wisepen-common \
    /app/services/wisepen-chat-service

COPY services/wisepen-common/pyproject.toml /app/services/wisepen-common/pyproject.toml
COPY services/wisepen-chat-service/pyproject.toml /app/services/wisepen-chat-service/pyproject.toml

# Install third-party dependencies only.
# Do not install workspace packages here, because their source code has not been copied yet.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package wisepen-chat-service --no-install-workspace

# Install Python Playwright Chromium before copying source code.
# This prevents normal Python source changes from invalidating the browser download layer.
#
# Important:
# - Do not mount /ms-playwright itself as a cache mount, because cache mount contents are not committed
#   into the final image layer.
# - Download into a BuildKit cache directory, then copy the cached browser files into /ms-playwright,
#   which is committed into this builder stage and later copied into the app stage.
RUN --mount=type=cache,target=/tmp/ms-playwright-cache \
    PLAYWRIGHT_BROWSERS_PATH=/tmp/ms-playwright-cache \
    /app/.venv/bin/python -m playwright install chromium \
    && rm -rf /ms-playwright \
    && mkdir -p /ms-playwright \
    && cp -a /tmp/ms-playwright-cache/. /ms-playwright/

# Install spaCy English model required by mem0.
# Keep this before COPY services/ so normal source changes do not re-run this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl \
    && /app/.venv/bin/python - <<'PY'
import spacy

spacy.load("en_core_web_sm")
print("en_core_web_sm loaded")
PY

# Preload Docling-related models for existing non-PDF office parsing into HF_HOME.
# PDF parsing/conversion must not depend on Docling.
# Keep this before COPY services/ so normal source changes do not re-run model preload.
RUN --mount=type=cache,target=/root/.cache/huggingface \
    /app/.venv/bin/python -c "from docling.document_converter import DocumentConverter; DocumentConverter()"

# Preload translation_assist Chinese-English OPUS-MT models into HF_HOME.
# Keep this before COPY services/ so normal source changes do not re-run model preload.
RUN --mount=type=cache,target=/root/.cache/huggingface \
    /app/.venv/bin/python - <<'PY'
from transformers import MarianMTModel, MarianTokenizer

models = [
    "Helsinki-NLP/opus-mt-zh-en",
    "Helsinki-NLP/opus-mt-en-zh",
]

for model_name in models:
    print(f"preloading {model_name}")
    MarianTokenizer.from_pretrained(model_name)
    MarianMTModel.from_pretrained(model_name)

print("translation models preloaded")
PY

# Copy source code last.
# Changing .py files should only invalidate layers after this point.
COPY services/ ./services/

# Defensive cleanup:
# Local virtual environments must never be reused inside Docker.
RUN find /app/services -type d \( -name ".venv" -o -name "venv" \) -prune -exec rm -rf {} +

# Install local workspace packages after source is copied.
# --inexact keeps explicitly installed packages such as en_core_web_sm instead of pruning them.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package wisepen-chat-service --inexact


FROM python:3.11-slim-bookworm AS app

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
ARG NODE_VERSION=20.18.3

ENV PATH="/app/.venv/bin:$PATH"
ENV DOCUMENT_TEMP_FILE_ROOT=/tmp/wisepen-chat-upload-files
ENV HF_HOME=/opt/wisepen/models/huggingface
ENV TRANSLATION_DEVICE=cpu
ENV DOCUMENT_EXPORT_PLAYWRIGHT_DISABLE_SANDBOX=true
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN mkdir -p /tmp/wisepen-chat-upload-files \
    && chmod 700 /tmp/wisepen-chat-upload-files

# Runtime subprocess dependencies:
# - pandoc: document conversion
# - node/npm: web_fetch LocalScriptFetcher runtime
# - curl/xz-utils: install Node.js binary
# - Chromium runtime libraries: required by rebrowser-playwright / Playwright
# - fonts-noto-cjk: required for Chinese page rendering and PDF/screenshot output
RUN apt-get update && apt-get install -y --no-install-recommends --fix-missing \
    curl \
    ca-certificates \
    xz-utils \
    pandoc \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libexpat1 \
    libxcb1 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    libgbm1 \
    libdrm2 \
    libxshmfence1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libfontconfig1 \
    libfreetype6 \
    fontconfig \
    fonts-liberation \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js for web_fetch local JS fetcher.
RUN curl -L --retry 3 --retry-delay 5 \
    https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    | tar -xJ -C /usr/local --strip-components=1 \
    && node --version \
    && npm --version

# Install web_fetch JS dependencies.
#
# Keep this before copying Python source/runtime artifacts.
# This keeps npm dependency installation cached when only Python source files change.
#
# Note:
# We intentionally do not set PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD globally here.
# Node-side rebrowser-playwright and Python Playwright can require different browser revisions.
# For correctness, Node-side browser handling should remain owned by the Node dependency layer,
# while Python Playwright's browser is preloaded in the builder stage and copied below.
COPY package.json package-lock.json /app/
RUN --mount=type=cache,target=/root/.npm \
    npm ci --omit=dev \
    && node -e "require('rebrowser-playwright'); require('jsdom'); require('turndown'); require('@mozilla/readability');" \
    && node -e "const { chromium } = require('rebrowser-playwright'); console.log('rebrowser-playwright chromium available:', Boolean(chromium));" \
    && pandoc --version >/dev/null

# Copy Python runtime artifacts and preloaded models.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /opt/wisepen/models /opt/wisepen/models

# Copy preloaded Python Playwright browser cache.
# This replaces the previous final-stage `python -m playwright install chromium`.
COPY --from=builder /ms-playwright /ms-playwright

# Python document_export uses Python Playwright.
# Browser binaries are already copied from builder; this layer only verifies runtime availability.
RUN python -c "from pathlib import Path; from tempfile import TemporaryDirectory; from playwright.sync_api import sync_playwright; tmp = TemporaryDirectory(); out = Path(tmp.name) / 'playwright-smoke.pdf'; p = sync_playwright().start(); browser = p.chromium.launch(headless=True, args=['--disable-dev-shm-usage', '--disable-gpu', '--no-sandbox']); page = browser.new_page(java_script_enabled=False); page.set_content('<html><body><h1>ok</h1></body></html>'); page.pdf(path=str(out), print_background=True); browser.close(); p.stop(); assert out.is_file() and out.stat().st_size > 0, 'Python Playwright PDF smoke test did not create a PDF'; tmp.cleanup()"

COPY --from=builder /app/services /app/services

WORKDIR /app/services/wisepen-chat-service/src

EXPOSE 9200

CMD ["uvicorn", "chat.main:app", "--host", "0.0.0.0", "--port", "9200"]
