# Small always-on host for the dashboard.
# Running it on a server rather than a laptop solves three things at once: a
# real URL for the client, a sync that keeps running when the laptop is shut,
# and no macOS file-permission wall.
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY METRICS.md README.md ./

# The database lives on a mounted volume so history survives redeploys.
ENV DB_PATH=/data/dashboard.db
VOLUME /data

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
