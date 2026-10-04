FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY vision /opt/lab-vision
RUN pip install --no-cache-dir . /opt/lab-vision

RUN useradd --create-home appuser && mkdir -p /data/uploads && chown -R appuser:appuser /data
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
