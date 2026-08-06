from django.shortcuts import redirect
from django.conf import settings


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
