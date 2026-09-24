FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

# Cloud Run injects PORT; default to 8000 for local docker runs.
ENV PORT=8000 \
    MCP_TRANSPORT=http \
    ICLOUD_MCP_LOCAL_FILES=false \
    PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/health || exit 1

# Stateless Streamable HTTP on 0.0.0.0:$PORT/mcp
CMD ["icloud-mcp", "--http"]
