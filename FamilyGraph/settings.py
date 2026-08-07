from pathlib import Path
import os
import importlib.util
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a project-local .env first, while retaining the former parent-directory
# location for existing deployments. Existing environment variables always win.
load_dotenv(BASE_DIR / '.env')
load_dotenv(BASE_DIR.parent / '.env')

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-7qj7fqbte&&b_-odt2@cg#h$jk86e6i5)1+1hc*q3bc#10ynar'
)

DEBUG = os.environ.get('DEBUG', 'True') == 'True'

if not DEBUG and SECRET_KEY.startswith('django-insecure-'):
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured('Set DJANGO_SECRET_KEY when DEBUG=False.')

_allowed_raw = os.environ.get('ALLOWED_HOSTS', '')
ALLOWED_HOSTS = (
    _allowed_raw.split(',') if _allowed_raw
    else (['*'] if DEBUG else ['localhost', '127.0.0.1'])
)

INSTALLED_APPS = [
    'jazzmin',
    'main',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

# ── Custom User Model ─────────────────────────────────────────
AUTH_USER_MODEL = 'main.User'

# ── Auth URLs & Redirects ──────────────────────────────────────
LOGIN_URL          = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# ── Session ──────────────────────────────────────────────────
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30   # 30 روز
SESSION_SAVE_EVERY_REQUEST = False

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'main.middleware.FeatureFlagMiddleware',
    'main.middleware.RequestObservabilityMiddleware',
    'main.middleware.WriteRateLimitMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'main.middleware.LoginRequiredMiddleware',
]
if not DEBUG and importlib.util.find_spec('whitenoise'):
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

ROOT_URLCONF = 'FamilyGraph.urls'

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly", # مثلا Flatly یا darkly یا هر گزینه دیگر
    "dark_mode_theme": "darkly", # اختیاری، فعال شدن دارک مود براساس تنظیمات کاربر
}

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS':  [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'builtins': ['main.templatetags.jalali_tags'],
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'FamilyGraph.wsgi.application'

if os.environ.get('USE_SQLITE') == '1':
    # موقت — فقط برای backup گرفتن از SQLite
    _sqlite_path = os.environ.get('SQLITE_DB_PATH', '')
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': (BASE_DIR / _sqlite_path) if _sqlite_path else (BASE_DIR / 'db.sqlite3'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE':   'django.db.backends.postgresql',
            'NAME':     os.environ.get('DB_NAME',     'familygraph'),
            'USER':     os.environ.get('DB_USER',     'postgres'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST':     os.environ.get('DB_HOST',     'localhost'),
            'PORT':     os.environ.get('DB_PORT',     '5432'),
            'OPTIONS': {
                'connect_timeout': 10,
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'fa'

TIME_ZONE = 'Asia/Tehran'

USE_I18N = True

USE_L10N = True

USE_TZ = True

STATIC_ROOT = BASE_DIR / "staticfiles"
if importlib.util.find_spec('whitenoise'):
    STORAGES = {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
    }

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Production deployment controls. They are intentionally inactive during local
# development and are configured through environment variables in hosting.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get('DATA_UPLOAD_MAX_MEMORY_SIZE', 10 * 1024 * 1024))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get('FILE_UPLOAD_MAX_MEMORY_SIZE', 10 * 1024 * 1024))

if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', '1') == '1'
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_REFERRER_POLICY = 'same-origin'
    if os.environ.get('BEHIND_HTTPS_PROXY', '1') == '1':
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    _trusted_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
    if _trusted_origins:
        CSRF_TRUSTED_ORIGINS = [
            origin.strip() for origin in _trusted_origins.split(',') if origin.strip()
        ]
    if os.environ.get('ENABLE_HSTS', '0') == '1':
        SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = True

# ── Cache (برای کش کردن جواب‌های AI) ──────────────────────────────────────
_redis_url = os.environ.get('REDIS_URL', '').strip()
if _redis_url:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _redis_url,
            'TIMEOUT': 21600,
            'OPTIONS': {'socket_connect_timeout': 2, 'socket_timeout': 2},
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': BASE_DIR / 'django_cache',
            'TIMEOUT': 21600,
            'OPTIONS': {'MAX_ENTRIES': 500},
        }
    }

WRITE_RATE_LIMIT = int(os.environ.get('WRITE_RATE_LIMIT', '120'))
WRITE_RATE_LIMIT_WINDOW = int(os.environ.get('WRITE_RATE_LIMIT_WINDOW', '60'))

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'verbose': {'format': '{asctime} {levelname} {name} request_id={request_id} {message}', 'style': '{'}},
    'filters': {'request_id': {'()': 'main.logging.RequestIdFilter'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'verbose', 'filters': ['request_id']}},
    'root': {'handlers': ['console'], 'level': os.environ.get('LOG_LEVEL', 'INFO')},
}
