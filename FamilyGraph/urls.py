from django.contrib import admin
from django.urls import path, include
from django.conf import settings

from main.views_media import protected_media

urlpatterns = [
    path('admin/', admin.site.urls),
    path(f'{settings.MEDIA_URL.strip("/")}/<path:path>', protected_media, name='protected_media'),
    path('', include('main.urls')),
]
