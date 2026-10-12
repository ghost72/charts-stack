FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=1027:100 app/ ./app/
RUN chmod -R a+rX /app

# Scheduler-Loop (kein Webserver): wartet bis zur Zielzeit, sendet dann die
# taegliche Zusammenfassung.
CMD ["python", "-u", "-m", "app.main"]
