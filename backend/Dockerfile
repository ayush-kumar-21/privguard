# Python 3.12: stable wheel coverage for psycopg / argon2-cffi / numpy / scipy.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Retry apt and pip: a single dropped connection on a slow link should not fail
# the whole image build.
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && apt-get update && apt-get install -y --no-install-recommends \
        libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 120 -r requirements.txt

COPY . .

EXPOSE 8000

# Migrations run at boot; then serve via gunicorn.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn privguard.wsgi:application --bind 0.0.0.0:8000"]
