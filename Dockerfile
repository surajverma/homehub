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
RUN npm install && npm run build:css

FROM python:3.12-alpine

WORKDIR /app

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

# Copy built Tailwind CSS from builder
COPY --from=builder /app/static/output.css /app/static/output.css

EXPOSE 5000

CMD ["gunicorn", "wsgi:app", "-w", "1", "-k", "sync", "-b", "0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-"]