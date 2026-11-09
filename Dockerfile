# The forge server: builds APIs with a headless browser and serves them all from one place.
# Base image ships Chromium + its system libraries; keep the pip playwright version in sync with it.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app
COPY pyproject.toml README.md ./
COPY apiforge ./apiforge
RUN pip install --no-cache-dir "playwright==1.49.0" . 

# Generated APIs, captures and key hashes live here: mount a persistent volume on /data.
ENV APIFORGE_HOME=/data/apis \
    PORT=8000
VOLUME ["/data"]
EXPOSE 8000
CMD ["sh", "-c", "uvicorn apiforge.server:app --host 0.0.0.0 --port ${PORT}"]
