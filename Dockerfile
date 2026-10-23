FROM python:3.10-slim-bookworm

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    TRACEGATE_DATA_DIR=/data \
    TRACEGATE_HOST=0.0.0.0 \
    TRACEGATE_PORT=8080

RUN groupadd --gid "${APP_GID}" tracegate \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin tracegate \
    && mkdir -p /app /data \
    && chown -R tracegate:tracegate /app /data

WORKDIR /app

COPY --chown=tracegate:tracegate pyproject.toml tracegate.py ./
COPY --chown=tracegate:tracegate tracegate ./tracegate

USER tracegate

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=2).read()"]

ENTRYPOINT ["python", "-m", "tracegate"]
CMD ["edge"]
