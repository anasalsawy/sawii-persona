FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SAWII_DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
VOLUME ["/data"]
EXPOSE 8800
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8800/health')"
CMD ["sawii-persona", "serve", "--port", "8800"]
