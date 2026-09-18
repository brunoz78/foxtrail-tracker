# Foxtrail-Tracker als Docker-Image (Anleitung: README.md, Abschnitt "Docker")
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FOXTRAIL_DB=/data/foxtrail.db \
    BIND=0.0.0.0:8080 \
    TZ=Europe/Zurich \
    PUID=1000 \
    PGID=1000

# tini: sauberes Beenden von Webserver und Zeitplan-Prozess; tzdata: Ortszeit fuer den Zeitplan
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY wsgi.py ./
COPY foxtrail ./foxtrail
COPY scripts/manage.py ./scripts/
COPY data/trails_seed.json ./data/
COPY docker/entrypoint.sh /usr/local/bin/foxtrail-entrypoint
COPY docker/foxtrailctl /usr/local/bin/foxtrailctl
RUN chmod 755 /usr/local/bin/foxtrail-entrypoint /usr/local/bin/foxtrailctl \
 && mkdir -p /data && chown "$PUID:$PGID" /data

VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['BIND'].rsplit(':',1)[1], timeout=4)"

ENTRYPOINT ["tini", "-g", "--", "foxtrail-entrypoint"]
CMD ["web"]
