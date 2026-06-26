from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file if it exists
load_dotenv(BASE_DIR.parent / '.env')

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-7qj7fqbte&&b_-odt2@cg#h$jk86e6i5)1+1hc*q3bc#10ynar'
)

DEBUG = os.environ.get('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = ['*'] if DEBUG else []

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
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'main.middleware.LoginRequiredMiddleware',
]

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
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
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

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

STATIC_ROOT = BASE_DIR / "staticfiles"

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Cache (برای کش کردن جواب‌های AI) ──────────────────────────────────────
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': BASE_DIR / 'django_cache',
        'TIMEOUT': 21600,   # 6 ساعت default
        'OPTIONS': {
            'MAX_ENTRIES': 500,
        }
    }
}
