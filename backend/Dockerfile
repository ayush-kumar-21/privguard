# Python 3.12: stable wheel coverage for psycopg / argon2-cffi / numpy / scipy.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Migrations run at boot; then serve via gunicorn.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn privguard.wsgi:application --bind 0.0.0.0:8000"]
