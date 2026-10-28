FROM python:3.12-slim

WORKDIR /app

# ffmpeg: for the FFT quality measurement (quality.py) of uploads - it measures
# the real frequency cutoff to detect upsampled/faked MP3s. Read/decode only,
# writes nothing. ffmpeg is available in every Debian repo.
# ffmpeg is additionally used by yt-dlp (YouTube tab) for the MP3 extraction and
# for embedding the thumbnail as cover art.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app files straight with the target ownership (youruser = 1027:100),
# since the container runs as this user via compose. Without it the files would
# be owned by root with 700 and uvicorn could not read the package (Permission
# denied).
COPY --chown=1027:100 app/ ./app/

# Additionally grant read/execute to everyone (robust, in case the container
# ends up running under a different UID).
RUN chmod -R a+rX /app

# Web UI + API (uvicorn). Port via compose (default 5056).
CMD ["python", "-u", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5056"]
