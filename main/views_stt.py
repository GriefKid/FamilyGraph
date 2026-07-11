"""
views_stt.py — تبدیل گفتار به متن (V8)

مسیر اصلی مرورگر Web Speech API است (بدون سرور).
این endpoint فقط fallback است برای مرورگرهایی که پشتیبانی ندارن:
صدا ضبط می‌شه، اینجا با Whisper (از طریق Groq — رایگان) تبدیل می‌شه.
"""
import os

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

MAX_SIZE = 15 * 1024 * 1024   # 15MB


@login_required
@csrf_exempt
def stt_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    audio = request.FILES.get('audio')
    if not audio:
        return JsonResponse({'error': 'فایل صوتی دریافت نشد'}, status=400)
    if audio.size > MAX_SIZE:
        return JsonResponse({'error': 'فایل صوتی بزرگ‌تر از ۱۵ مگابایته'}, status=400)

    groq_key = os.environ.get('GROQ_API_KEY', '')
    openai_key = os.environ.get('OPENAI_API_KEY', '')

    try:
        from openai import OpenAI
        if groq_key:
            client = OpenAI(base_url='https://api.groq.com/openai/v1', api_key=groq_key)
            model = 'whisper-large-v3'
        elif openai_key:
            client = OpenAI(api_key=openai_key)
            model = 'whisper-1'
        else:
            return JsonResponse({
                'error': 'مرورگرت تشخیص گفتار داخلی نداره و کلید Whisper هم تنظیم نشده. '
                         'یا از Chrome/Edge استفاده کن، یا GROQ_API_KEY بذار '
                         '(console.groq.com — رایگان، Whisper هم داره).'
            }, status=503)

        resp = client.audio.transcriptions.create(
            model=model,
            file=(audio.name or 'voice.webm', audio.read()),
            language='fa',
        )
        text = (getattr(resp, 'text', '') or '').strip()
        if not text:
            return JsonResponse({'error': 'چیزی تشخیص داده نشد — واضح‌تر حرف بزن'}, status=422)
        return JsonResponse({'ok': True, 'text': text})
    except Exception as e:
        msg = str(e)
        if '429' in msg or 'rate' in msg.lower():
            return JsonResponse({'error': 'سهمیه‌ی امروز Whisper تموم شده — فردا دوباره'}, status=500)
        return JsonResponse({'error': f'خطای تبدیل گفتار: {msg[:150]}'}, status=500)
