from django.shortcuts import redirect
from django.conf import settings
import time
import uuid
from django.http import JsonResponse


class RequestObservabilityMiddleware:
    """Attach a request id and store only bounded, non-body operational metadata."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request.headers.get('X-Request-ID', '')[:36] or str(uuid.uuid4())
        started = time.monotonic()
        try:
            response = self.get_response(request)
        except Exception as exc:
            self._record(request, 'error', type(exc).__name__, str(exc)[:500], None)
            raise
        duration = round((time.monotonic() - started) * 1000)
        response['X-Request-ID'] = request.request_id
        if response.status_code >= 500 or duration >= 2000:
            self._record(request, 'error' if response.status_code >= 500 else 'warning',
                         f'http_{response.status_code}', 'Request telemetry', duration)
        return response

    @staticmethod
    def _record(request, level, code, message, duration):
        try:
            from .models import ObservabilityEvent
            ObservabilityEvent.objects.create(
                request_id=request.request_id, owner=request.user if request.user.is_authenticated else None,
                level=level, area='backend', path=request.path[:240], code=code[:100],
                message=message, duration_ms=duration,
                metadata={'method': request.method})
        except Exception:
            pass


class FeatureFlagMiddleware:
    PATH_FLAGS = (
        ('/api/memory/assistant/', 'relationship-suggestions'),
        ('/psychology/', 'psychology-memory'),
        ('/api/psychology/', 'psychology-memory'),
        ('/social/', 'social-network'),
        ('/api/social/', 'social-network'),
        ('/service-worker.js', 'pwa'),
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        flag_name = next((flag for prefix, flag in self.PATH_FLAGS if request.path.startswith(prefix)), None)
        if flag_name:
            try:
                from .models import FeatureFlag
                flag = FeatureFlag.objects.filter(name=flag_name).first()
                if flag and not flag.is_enabled_for(request.user):
                    return JsonResponse({'error': 'این قابلیت فعلاً غیرفعال است.', 'feature': flag_name}, status=404)
            except Exception:
                pass
        return self.get_response(request)


class LoginRequiredMiddleware:
    """
    همه URLها نیاز به login دارن مگه اینکه در لیست استثنا باشن.
    """
    EXEMPT = (
        '/login/',
        '/register/',
        '/logout/',
        '/api/captcha/',
        '/static/',
        '/media/',
        '/admin/',
        '/favicon.ico',
        '/service-worker.js',
        '/api/system/health/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            path = request.path
            exempt = any(path.startswith(p) for p in self.EXEMPT)
            if not exempt:
                login_url = settings.LOGIN_URL
                if '?' not in login_url:
                    login_url += f'?next={path}'
                return redirect(login_url)
        return self.get_response(request)
