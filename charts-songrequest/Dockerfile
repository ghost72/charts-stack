FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App-Dateien direkt mit der Ziel-Ownership (youruser = 1027:100) kopieren, da der
# Container via compose als dieser User laeuft. Ohne das gehoeren die Dateien
# root mit 700 und uvicorn kann das Package nicht lesen (Permission denied).
COPY --chown=1027:100 app/ ./app/

# Zusaetzlich Lese-/Ausfuehrrechte fuer alle setzen (robust, falls der Container
# doch unter einer anderen UID laeuft).
RUN chmod -R a+rX /app

# Web-UI + API (uvicorn). Port via compose (Default 5056).
CMD ["python", "-u", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5056"]
