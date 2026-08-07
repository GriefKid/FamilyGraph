# Deploy

1. Copy `.env.example` to `.env`; set `DEBUG=False`, a strong `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`, `DB_PASSWORD`, and optional AI keys.
2. Run `docker compose up --build -d`.
3. Verify `/api/system/health/` returns database `ok`.
4. Back up the PostgreSQL volume and uploaded media independently. User-level encrypted exports are available under **بکاپ و ابزارها**.

Production HTTPS is required for secure cookies, PWA notifications, and service workers outside localhost.
