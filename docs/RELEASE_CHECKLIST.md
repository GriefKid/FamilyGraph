# چک‌لیست انتشار FamilyGraph

## قبل از اولین deploy

- یک دامنه و HTTPS واقعی آماده کن.
- `.env.production.example` را به secretهای سرویس deploy منتقل کن؛ خود فایل `.env` هرگز commit نشود.
- `DJANGO_SECRET_KEY` را با کلید تصادفی حداقل ۵۰ کاراکتری بساز.
- کلیدهایی که در چت، issue یا log دیده شده‌اند را از پنل provider revoke و دوباره ایجاد کن.
- `ALLOWED_HOSTS` و `CSRF_TRUSTED_ORIGINS` را برای دامنهٔ واقعی تنظیم کن.
- SMTP را برای password reset تنظیم و ارسال ایمیل را با یک حساب آزمایشی بررسی کن.
- PostgreSQL، Redis و storage پایدار برای `media/` داشته باش.

## Deploy

```bash
docker compose build
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
docker compose exec web python manage.py release_preflight
curl -f https://YOUR_DOMAIN/api/system/health/
```

`release_preflight` باید بدون FAIL تمام شود. بعد از deploy تست‌های smoke را روی staging اجرا کن:

```bash
pip install -r requirements-dev.txt
playwright install chromium
E2E_BASE_URL=https://staging.example.com pytest e2e/test_smoke.py
```

## Backup و restore

برای disaster recovery، backup اصلی باید از خود PostgreSQL و storage باشد؛ فایل backup داخل پنل برای انتقال دادهٔ یک کاربر است، جایگزین backup دیتابیس production نیست.

```bash
mkdir -p backups
docker compose exec -T db pg_dump -U familygraph -d familygraph | gzip > backups/familygraph-$(date +%F).sql.gz
```

فایل‌های `media/` را نیز با retention جداگانه به storage امن و رمزگذاری‌شده کپی کن. حداقل ماهی یک‌بار restore آزمایشی روی دیتابیس جدا انجام بده:

```bash
gunzip -c backups/familygraph-YYYY-MM-DD.sql.gz | docker compose exec -T db psql -U familygraph -d familygraph_restore
```

## بعد از انتشار

- `/api/system/health/` و خطاهای server را مانیتور کن.
- مصرف و quota provider AI را روزانه بررسی کن.
- یک حساب آزمایشی بساز، login، reset password، upload، چت، export، backup و حذف حساب را دستی بررسی کن.
- متن این صفحه و صفحهٔ حریم خصوصی را با نام مالک محصول، ایمیل پشتیبانی، محل نگهداری داده و قوانین محل انتشار تکمیل کن.
