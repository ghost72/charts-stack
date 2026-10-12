FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=1027:100 app/ ./app/
RUN chmod -R a+rX /app

# Der Service laeuft als Scheduler-Loop (kein Webserver noetig).
CMD ["python", "-u", "-m", "app.main"]
