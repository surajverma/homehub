FROM python:3.12-alpine AS builder

WORKDIR /app

# Build dependencies
RUN apk add --no-cache \
    build-base \
    zlib-dev \
    jpeg-dev \
    nodejs \
    npm

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy package files and build Tailwind CSS
COPY package.json tailwind.config.js ./
COPY static/input.css ./static/
COPY templates ./templates
# Page scripts use Tailwind classes too, so Tailwind must see them when it builds the CSS
COPY static/js ./static/js
RUN npm install && npm run build:css

FROM python:3.12-alpine

WORKDIR /app

# Build argument for app version (injected by CI) and environment variable for runtime
ARG APP_VERSION=dev
ENV SW_CACHE_VERSION=$APP_VERSION
# Log lines reach `docker logs` as they are written
ENV PYTHONUNBUFFERED=1

# Runtime-only packages
RUN apk add --no-cache \
    ffmpeg \
    ghostscript \
    libjpeg-turbo \
    zlib \
    libstdc++

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . /app

# Compile the translation catalogs (.po) into the .mo files the app reads
RUN pybabel compile -d translations

# Copy built Tailwind CSS from builder
COPY --from=builder /app/static/output.css /app/static/output.css

EXPOSE 5000

# /healthz answers without a login and checks the database
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:5000/healthz || exit 1

# One process (SQLite, in-process download threads) with threads so a slow upload or PDF job does not block everyone
CMD ["gunicorn", "wsgi:app", "-w", "1", "-k", "gthread", "--threads", "4", "-b", "0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-"]