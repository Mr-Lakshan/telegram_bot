FROM python:3.11-slim
WORKDIR /app
RUN apt-get update -qq && apt-get install -y -qq fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
CMD ["python", "app.py"]
