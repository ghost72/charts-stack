FROM python:3.12-slim

WORKDIR /app

# ffmpeg: wird vom pip-Paket r128gain genutzt, um die Lautheit (EBU R128) zu
# messen und ReplayGain-2.0-Tags zu schreiben (Lautstaerke-Normalisierung).
# Schreibt nur Metadaten-Tags, der Audio-Stream bleibt unveraendert. ffmpeg ist
# in jedem Debian-Repo verfuegbar.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=1027:100 app/ ./app/
# Einmal-/Wartungsskripte (cleanup_flashback_legacy.py, strip_provenance_tags.py)
# mit ins Image, damit sie im Container unter /app/scripts/ verfuegbar sind.
COPY --chown=1027:100 scripts/ ./scripts/
RUN chmod -R a+rX /app

# Der Service laeuft als Scheduler-Loop (kein Webserver noetig).
CMD ["python", "-u", "-m", "app.main"]
