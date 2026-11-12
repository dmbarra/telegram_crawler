FROM python:3.12-slim

# ffmpeg → video thumbnails in the viewer
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY src ./src
COPY viewer ./viewer

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /downloads /data \
    && chown app:app /downloads /data
USER app

ENV PYTHONUNBUFFERED=1 \
    DOWNLOADS_DIR=/downloads \
    DATA_DIR=/data \
    VIEWER_HOST=0.0.0.0 \
    VIEWER_PORT=5000

EXPOSE 5000
CMD ["python", "main.py"]
