FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface

WORKDIR /app

# Tesseract is used when a PDF/image does not contain extractable text.
# libgomp1 is needed by common ML wheels used by the retrieval stack.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY data ./data
COPY README.md .env.example ./

RUN useradd --create-home --shell /usr/sbin/nologin claims \
    && mkdir -p /app/.cache/huggingface \
    && chown -R claims:claims /app

USER claims

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
