FROM python:3.11-alpine AS builder

WORKDIR /app

# Build dependencies
RUN apk add --no-cache \
    build-base \
    zlib-dev \
    jpeg-dev

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-alpine

WORKDIR /app

# Runtime-only packages
RUN apk add --no-cache \
    ffmpeg \
    ghostscript \
    libjpeg-turbo \
    zlib \
    libstdc++

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . /app

EXPOSE 5005

CMD ["gunicorn", "wsgi:app", "-w", "1", "-k", "sync", "-b", "0.0.0.0:5005", "--access-logfile", "-", "--error-logfile", "-"]