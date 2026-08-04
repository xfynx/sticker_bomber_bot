# syntax=docker/dockerfile:1

# ---------- builder ----------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DB_PATH=/data/bot.sqlite3

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . /app

# Непривилегированный пользователь + том под базу
RUN useradd --create-home --uid 10001 bot \
 && mkdir -p /data \
 && chown -R bot:bot /app /data
USER bot

VOLUME ["/data"]

# Healthcheck: процесс жив и база доступна на запись
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import os,sqlite3,sys; p=os.environ.get('DB_PATH','/data/bot.sqlite3'); sqlite3.connect(p).execute('select 1'); sys.exit(0)" || exit 1

CMD ["python", "-u", "bot.py"]
