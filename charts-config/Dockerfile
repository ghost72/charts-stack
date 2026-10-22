FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app files with the target ownership (youruser = 1027:100) directly,
# since the container runs as that user via compose. Without this the files are
# owned by root with 700 and uvicorn cannot read the package (permission denied).
COPY --chown=1027:100 app/ ./app/

# Additionally grant read/execute for everyone (robust, in case the container
# ends up running under a different UID).
RUN chmod -R a+rX /app

# Web UI + API (uvicorn). Port via compose (default 5058).
CMD ["python", "-u", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5058"]
