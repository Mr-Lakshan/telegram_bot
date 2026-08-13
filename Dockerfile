FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# The code opens "bot_data.db" relative to /app in about twenty places. Rather
# than change all of them, /app/bot_data.db becomes a symlink into the mounted
# directory. SQLite resolves the symlink and creates bot_data.db-wal and -shm
# beside the real file, which is the whole point — those two files have to live
# on the host so every container shares them.
#
# /app/dbdata is deliberately NOT created here. If the mount is ever missing the
# symlink dangles and SQLite fails loudly with "unable to open database file",
# instead of quietly starting a fresh empty database inside the container. That
# silent version is exactly the bug this replaces.
RUN rm -f /app/bot_data.db && ln -s /app/dbdata/bot_data.db /app/bot_data.db

CMD ["python","-u","-m","bot.main"]