FROM ghcr.io/astral-sh/uv:0.6-python3.11-bookworm-slim AS builder

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY services/wisepen-common/pyproject.toml services/wisepen-common/pyproject.toml
COPY services/wisepen-chat-service/pyproject.toml services/wisepen-chat-service/pyproject.toml

RUN uv sync --frozen --no-dev --no-install-workspace --directory services/wisepen-chat-service

COPY services/ services/

FROM builder AS builder-lite
RUN uv sync --frozen --no-dev --directory services/wisepen-chat-service

FROM builder AS builder-full
ARG HF_TOKEN
ENV HF_HOME=/app/.hf_cache
RUN uv sync --frozen --no-dev --group doc --directory services/wisepen-chat-service
RUN --mount=type=secret,id=hf_token,env=HF_TOKEN \
    uv run --directory services/wisepen-chat-service python -c "from docling.document_converter import DocumentConverter; DocumentConverter()"


FROM python:3.11-slim-bookworm AS lite
WORKDIR /app
COPY --from=builder-lite /app/.venv /app/.venv
COPY --from=builder-lite /app/services /app/services
ENV PATH="/app/.venv/bin:$PATH"
ENV DOCUMENT_PARSER_BACKEND=native
WORKDIR /app/services/wisepen-chat-service/src
EXPOSE 8000
CMD ["uvicorn", "chat.main:app", "--host", "0.0.0.0", "--port", "9200"]

FROM python:3.11-slim-bookworm AS full
WORKDIR /app
COPY --from=builder-full /app/.venv /app/.venv
COPY --from=builder-full /app/services /app/services
COPY --from=builder-full /app/.hf_cache /app/.hf_cache
ENV PATH="/app/.venv/bin:$PATH"
ENV DOCUMENT_PARSER_BACKEND=docling
ENV HF_HOME=/app/.hf_cache
WORKDIR /app/services/wisepen-chat-service/src
EXPOSE 8000
CMD ["uvicorn", "chat.main:app", "--host", "0.0.0.0", "--port", "9200"]