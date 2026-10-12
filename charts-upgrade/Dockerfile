FROM python:3.12-slim

WORKDIR /app

# ffmpeg: wird von quality.py genutzt, um den echten Frequenz-Cutoff
# (aspectralstats/rolloff) zu messen und so upgesampelte/gefaelschte MP3s zu
# erkennen. In jedem Debian-Repo verfuegbar.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=1027:100 app/ ./app/
RUN chmod -R a+rX /app

# Scheduler-Loop (kein Webserver).
CMD ["python", "-u", "-m", "app.main"]
