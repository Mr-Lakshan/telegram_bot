FROM python:3.11-slim
WORKDIR /app
RUN apt-get update -qq && apt-get install -y -qq fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# Same reason as the root Dockerfile: app.py reads DB_PATH with a default of
# /app/bot_data.db, and that has to resolve into the shared directory so the
# -wal and -shm files land on the host with everyone else's.
RUN rm -f /app/bot_data.db && ln -s /app/dbdata/bot_data.db /app/bot_data.db

CMD ["python", "app.py"]