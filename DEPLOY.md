# Deploy

1. Copy `.env.example` to `.env`; set `DEBUG=False`, a strong `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`, `DB_PASSWORD`, and optional AI keys.
2. Run `docker compose up --build -d`.
3. Verify `/api/system/health/` returns both database and cache `ok`.
4. Back up the PostgreSQL volume and uploaded media independently. User-level encrypted exports are available under **بکاپ و ابزارها**.

Production HTTPS is required for secure cookies, PWA notifications, and service workers outside localhost.

Configure a daily provider snapshot for the PostgreSQL volume and uploaded media, and test a restore before inviting users. Redis is only a cache and does not need to be restored. Put a TLS-terminating reverse proxy in front of port 8000 and set `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `BEHIND_HTTPS_PROXY=1`, and `ENABLE_HSTS=1` after HTTPS is verified.

The Compose stack includes PostgreSQL and Redis. Redis provides shared caching and the write-rate limiter used across web workers. It is optional for local development: omit `REDIS_URL` to use the local file cache.
