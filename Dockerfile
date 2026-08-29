# Образ серверного экземпляра meet-archive.
#
# Локальные запускаторы (serve.sh, .app, LaunchAgent) сюда не едут: они завязаны
# на macOS, TCC и launchd. На Linux работает только этот путь.
FROM python:3.12-slim

# UID фиксирован: том /opt/meet-archive/data на хосте должен принадлежать ему же.
# Открытие WAL-базы даже в режиме mode=ro требует права записи в каталог ради
# файла -shm, поэтому несовпадение владельца — самая вероятная ошибка первого
# развёртывания, а не мелочь.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY summit-archive-admin/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY summit-archive-admin/ /app/

USER app

ENV MEET_ARCHIVE_ROLE=server \
    SUMMIT_ARCHIVE_ROOT=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ingest/health', timeout=4)"

# 0.0.0.0 здесь не нарушает инвариант «только loopback» из CLAUDE.md: порт не
# публикуется на хост, контейнер доступен лишь в сети Traefik, а TLS и Basic Auth
# стоят перед ним. Инвариант loopback остаётся в силе для локального serve.sh.
#
# --workers 1 намеренно: писатель у базы должен быть один. WAL выдержал бы больше,
# но один процесс делает рассуждение о блокировках тривиальным.
CMD ["uvicorn", "app:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
