FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY frontend frontend
EXPOSE 8000
# Render (and most hosts) pass the port in $PORT; default to 8000 for local/docker-compose
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
