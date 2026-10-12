FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
RUN chmod -R a+rX /app

# Laeuft als root (Default), damit der Docker-Socket (root:docker) lesbar ist -
# das Dashboard fragt nur Zustaende ab (inspect), aendert keine Container.
# Web-UI + API (uvicorn). Port via compose (Default 5057).
CMD ["python", "-u", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5057"]
