FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SAWII_DATA_DIR=/data
WORKDIR /app
# openhands-sdk (the "hands" of B) wants git available for its tool stack
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
COPY prompts ./prompts
# Install core deps. The [deep] extra pulls openhands-sdk so B has shell + file-editor hands in the
# workshop; if pip can't resolve it the core install still succeeds and the deep lane falls back
# to LLMWorker (B still plans and critiques, just no terminal access).
RUN pip install --no-cache-dir . && (pip install --no-cache-dir ".[deep]" || echo "openhands-sdk unavailable; LLMWorker fallback")
# Railway Volumes mount at runtime; the Dockerfile must NOT declare VOLUME.
EXPOSE 8800
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8800/health')"
CMD ["sawii-persona", "serve", "--port", "8800"]
