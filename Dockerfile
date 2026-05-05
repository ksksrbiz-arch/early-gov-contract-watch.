FROM python:3.11-slim

WORKDIR /app

# System deps for pandas / yfinance wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Production: gunicorn serves the Flask app behind Render + Cloudflare.
# IMPORTANT: workers=1 is intentional — the BotController is an in-process
# singleton owning a thread, state counters, and a log buffer. Multiple
# workers would each spawn their own bot loop, double-trade, and split
# logs. We use threads for request concurrency instead. If you need to
# horizontally scale, factor the bot loop into a separate worker process.
EXPOSE 8000
ENV PORT=8000 \
    GUNICORN_WORKERS=1 \
    GUNICORN_THREADS=8 \
    GUNICORN_TIMEOUT=120

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} \
    --workers ${GUNICORN_WORKERS} \
    --threads ${GUNICORN_THREADS} \
    --timeout ${GUNICORN_TIMEOUT} \
    --access-logfile - \
    --error-logfile - \
    wsgi:app"]
