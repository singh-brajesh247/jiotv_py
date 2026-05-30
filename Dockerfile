FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    JIOTV_PATH_PREFIX=/data \
    JIOTV_LOG_TO_STDOUT=true

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system jiotv \
    && adduser --system --ingroup jiotv --home /home/jiotv jiotv \
    && mkdir -p /data \
    && chown -R jiotv:jiotv /data /home/jiotv

COPY requirements-python.txt /tmp/requirements-python.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements-python.txt

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

COPY --chown=jiotv:jiotv . ./jiotv_py

EXPOSE 5001
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/healthz', timeout=3).close()"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "jiotv_py", "serve", "--host", "0.0.0.0", "--port", "5001", "--log-stdout"]
