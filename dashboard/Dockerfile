# Dashboard micro-service — Cloud Run. Read-only view over the live Google Sheet.
# Independent of the OCR server image (no tesseract, no heavy vision deps).
# Build context is the repo root:  docker build -f dashboard/Dockerfile .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY dashboard/requirements.txt ./dashboard/requirements.txt
RUN pip install --upgrade pip && pip install -r dashboard/requirements.txt

# Only what the service needs. services/ is pure-python; imported from P1 onward.
COPY dashboard/ ./dashboard/
COPY services/ ./services/

# Cloud Run injects $PORT (default 8080). JSON form + sh -c so $PORT still
# expands and SIGTERM still reaches uvicorn (exec).
ENV PORT=8080
CMD ["sh", "-c", "exec uvicorn dashboard.app:app --host 0.0.0.0 --port ${PORT}"]
