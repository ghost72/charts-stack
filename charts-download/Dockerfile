FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=1027:100 app/ ./app/
# App-Dateien fuer den Container-User (1027:100) lesbar machen. Ohne das crasht
# der Start mit PermissionError, weil der Build als root kopiert.
RUN chmod -R a+rX /app

# Scheduler-Loop (kein Webserver).
CMD ["python", "-u", "-m", "app.main"]
