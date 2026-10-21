FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ .

# Ship the CLI so the backend can serve it for download (GET /api/cli/download)
COPY cli/sab /app/cli/sab

# Downloadable Mac USB helper; runs on the user's Mac, never in this Linux container.
COPY tools/ /app/tools/

# Ship public knowledge-base tooling and examples only. Local business documents
# and runtime configuration are excluded by .dockerignore.
COPY test_knowledge /app/test_knowledge

RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
