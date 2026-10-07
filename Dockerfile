FROM python:3.14-slim

ARG POWERLAB_VERSION=dev
LABEL org.opencontainers.image.version="${POWERLAB_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    POWERLAB_VERSION=${POWERLAB_VERSION} \
    PYTHONUNBUFFERED=1 \
    POWERLAB_HOST=0.0.0.0 \
    POWERLAB_PORT=8889 \
    POWERLAB_DATA_DIR=/data \
    POWERLAB_DATABASE_URL=sqlite:////data/powerlab.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN mkdir -p /data

EXPOSE 8889

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8889/', timeout=3).close()"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8889"]
