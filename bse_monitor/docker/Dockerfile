# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

# tesseract-ocr  : OCR fallback for scanned filings
# poppler-utils  : pdftoppm, used by some pdfplumber paths
# libgl1/libglib : PyMuPDF rasterisation
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libgl1 \
        libglib2.0-0 \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY bse_monitor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -m spacy download en_core_web_sm

# Chromium for the Playwright fallback. Skip by building with
# --build-arg INSTALL_BROWSER=0 if the deployment never needs it.
ARG INSTALL_BROWSER=1
RUN if [ "$INSTALL_BROWSER" = "1" ]; then playwright install --with-deps chromium; fi

COPY bse_monitor /app/bse_monitor

RUN mkdir -p /app/data/pdfs /app/data/exports /app/data/logs /app/data/models \
    && useradd --create-home --uid 10001 monitor \
    && chown -R monitor:monitor /app
USER monitor

HEALTHCHECK --interval=60s --timeout=15s --start-period=20s --retries=3 \
    CMD python -c "from bse_monitor.config import Config; Config.load()" || exit 1

ENTRYPOINT ["python", "-m", "bse_monitor.main"]
CMD ["schedule"]
