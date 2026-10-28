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

# Static files for the Django admin, served by WhiteNoise. Dummy settings: collectstatic touches no database.
RUN DEBUG=True SECRET_KEY=build PRIVGUARD_SERVER_SECRET=build DATABASE_URL=postgres://u:p@localhost:1/x python manage.py collectstatic --noinput

EXPOSE 8000

# Hosted start: migrate, optionally seed, then gunicorn on $PORT (see start.sh).
CMD ["./start.sh"]
