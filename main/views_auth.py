"""
views_auth.py — احراز هویت V3
login, register (multi-step), logout, profile, captcha
"""
import random
import re
from datetime import date

from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET

User = get_user_model()


# ─────────────────────────────────────────────────────────────────
# Sync helpers
# ─────────────────────────────────────────────────────────────────

def _trigger_new_user_sync(new_user):
    """
    بعد از ثبت‌نام کاربر جدید (public)، دنبال نودهای مشابه در شبکه‌های دیگران می‌گرده
    و SyncNotification می‌سازه: «آیا این همون شخصه؟»
    """
    if not new_user.is_public:
        return
    from django.db.models import Q
    from main.models import Node, SyncNotification

    qs = Node.objects.filter(
        Q(username__iexact=new_user.username) |
        (
            Q(first_name__iexact=new_user.first_name) &
            Q(last_name__iexact=new_user.last_name) &
            ~Q(first_name='') & ~Q(last_name='')
        )
    ).exclude(owner=new_user).select_related('owner')

    for node in qs:
        if not node.owner:
            continue
        # جلوگیری از duplicate
        if SyncNotification.objects.filter(
            recipient=node.owner,
            from_user=new_user,
            node_username=node.username,
            event_type='new_user',
            status='pending',
        ).exists():
            continue
        SyncNotification.objects.create(
            recipient=node.owner,
            from_user=new_user,
            node_username=node.username,
            event_type='new_user',
            payload={
                'node_id':          node.id,
                'new_user_username': new_user.username,
                'new_user_name':    f"{new_user.first_name} {new_user.last_name}".strip() or new_user.username,
                'first_name':       new_user.first_name,
                'last_name':        new_user.last_name,
                'career':           new_user.career,
                'city':             new_user.city,
            }
        )


def _trigger_profile_update_sync(user):
    """
    بعد از آپدیت پروفایل عمومی، به کسایی که نودشون رو از این کاربر import کردن notify می‌ده.
    """
    if not user.is_public:
        return
    from main.models import Node, SyncNotification

    imported_nodes = Node.objects.filter(
        imported_from=user
    ).exclude(owner=user).select_related('owner')

    payload = {
        'username':   user.username,
        'first_name': user.first_name,
        'last_name':  user.last_name,
        'career':     user.career,
        'city':       user.city,
    }

    for node in imported_nodes:
        if not node.owner:
            continue
        # dedup: اگه قبلاً pending notification برای همین جفت وجود داره، skip کن
        if SyncNotification.objects.filter(
            recipient=node.owner,
            from_user=user,
            node_username=node.username,
            event_type='profile_update',
            status='pending',
        ).exists():
            continue
        SyncNotification.objects.create(
            recipient=node.owner,
            from_user=user,
            node_username=node.username,
            event_type='profile_update',
            payload=payload,
        )


# ─────────────────────────────────────────────────────────────────
# کپچای ریاضی
# ─────────────────────────────────────────────────────────────────

FA_DIGITS = '۰۱۲۳۴۵۶۷۸۹'

def _fa(n: int) -> str:
    return ''.join(FA_DIGITS[int(d)] for d in str(n))

def _new_captcha(request) -> str:
    """یک سوال ریاضی جدید می‌سازه و در session ذخیره می‌کنه."""
    ops = ['+', '-', '×']
    op = random.choice(['+', '+', '+', '-', '×'])   # + بیشتر
    a = random.randint(2, 9)
    if op == '+':
        b = random.randint(1, 9)
        answer = a + b
        q = f'{_fa(a)} + {_fa(b)}'
    elif op == '-':
        b = random.randint(1, a)
        answer = a - b
        q = f'{_fa(a)} − {_fa(b)}'
    else:
        b = random.randint(2, 5)
        answer = a * b
        q = f'{_fa(a)} × {_fa(b)}'
    request.session['captcha_answer']   = answer
    request.session['captcha_question'] = q
    return q


def _check_captcha(request) -> bool:
    try:
        user_ans = int(request.POST.get('captcha', '').strip())
    except ValueError:
        return False
    return user_ans == request.session.get('captcha_answer')


# ─────────────────────────────────────────────────────────────────
# API: رفرش کپچا
# ─────────────────────────────────────────────────────────────────

@require_GET
def captcha_refresh(request):
    q = _new_captcha(request)
    return JsonResponse({'question': q})


# ─────────────────────────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('/')

    error = None

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        remember = request.POST.get('remember_me', '')

        # کپچا
        if not _check_captcha(request):
            error = 'جواب سوال ریاضی اشتباهه. دوباره امتحان کن.'
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                # شاید با email لاگین کرده
                try:
                    u = User.objects.get(email=username)
                    user = authenticate(request, username=u.username, password=password)
                except User.DoesNotExist:
                    pass

            if user is None:
                error = 'نام کاربری یا رمز عبور اشتباهه.'
            elif not user.is_active:
                error = 'حساب غیرفعاله. با پشتیبانی تماس بگیر.'
            else:
                login(request, user)
                if not remember:
                    request.session.set_expiry(0)   # تا بستن مرورگر
                # جلوگیری از open redirect — فقط URLهای داخلی مجاز
                next_url = request.GET.get('next', '/')
                if not next_url.startswith('/') or next_url.startswith('//'):
                    next_url = '/'
                # BUGFIX: اگه next به مسیری اشاره کنه که وجود نداره (مثلاً
                # صفحه‌ی نودی که پاک شده)، به جای 404 برو خونه
                try:
                    from django.urls import resolve
                    resolve(next_url.split('?')[0])
                except Exception:
                    next_url = '/'
                return redirect(next_url)

        _new_captcha(request)   # captcha جدید بعد از خطا

    captcha_q = _new_captcha(request) if request.method == 'GET' else request.session.get('captcha_question', _new_captcha(request))

    return render(request, 'auth/login.html', {
        'error':      error,
        'captcha_q':  captcha_q,
    })


# ─────────────────────────────────────────────────────────────────
# Register — multi-step (step در session)
# ─────────────────────────────────────────────────────────────────

def _validate_password(pw: str) -> str | None:
    """اگه اشکال داشت پیام خطا برمی‌گردونه، وگرنه None."""
    if len(pw) < 8:
        return 'رمز عبور باید حداقل ۸ کاراکتر باشه.'
    if not re.search(r'[A-Z]', pw):
        return 'رمز عبور باید حداقل یک حرف بزرگ انگلیسی داشته باشه.'
    if not re.search(r'\d', pw):
        return 'رمز عبور باید حداقل یک عدد داشته باشه.'
    return None


def _clean_registration_text(request, field: str, limit: int) -> str:
    """Keep onboarding answers bounded before storing them in the session."""
    return request.POST.get(field, '').strip()[:limit]


def _split_profile_items(raw: str) -> list[str]:
    """Turn a comma/newline-separated onboarding answer into clean AI signals."""
    items = []
    for item in re.split(r'[,،\n]+', raw):
        item = item.strip()
        if item and item not in items:
            items.append(item[:80])
    return items[:12]


def register_view(request):
    if request.user.is_authenticated:
        return redirect('/')

    step = int(request.POST.get('step', request.GET.get('step', '1')))
    reg  = request.session.get('reg_data', {})
    error = None

    # ── Step 1: مشخصات اصلی ────────────────────────────────────
    if request.method == 'POST' and step == 1:
        username = request.POST.get('username', '').strip()
        email    = request.POST.get('email', '').strip()
        pw1      = request.POST.get('password', '')
        pw2      = request.POST.get('password2', '')

        if not username or not re.match(r'^[a-zA-Z0-9_.-]{3,30}$', username):
            error = 'نام کاربری باید ۳ تا ۳۰ کاراکتر (حروف انگلیسی، عدد، _ - .) باشه.'
        elif User.objects.filter(username=username).exists():
            error = 'این نام کاربری قبلاً ثبت شده.'
        elif email and User.objects.filter(email=email).exists():
            error = 'این ایمیل قبلاً ثبت شده.'
        elif pw1 != pw2:
            error = 'رمز عبور و تکرارش یکی نیستن.'
        else:
            err = _validate_password(pw1)
            if err:
                error = err
            else:
                from django.core import signing
                reg.update({
                    'username':       username,
                    'email':          email,
                    'password_token': signing.dumps(pw1, salt='register-pw'),
                })
                request.session['reg_data'] = reg
                return redirect('/register/?step=2')

    # ── Step 2: پروفایل و سیگنال‌های اولیه برای AI (اختیاری) ──
    elif request.method == 'POST' and step == 2:
        reg.update({
            'first_name':          _clean_registration_text(request, 'first_name', 150),
            'last_name':           _clean_registration_text(request, 'last_name', 150),
            'birth_date':          _clean_registration_text(request, 'birth_date', 10),
            'career':              _clean_registration_text(request, 'career', 200),
            'city':                _clean_registration_text(request, 'city', 100),
            'country':             _clean_registration_text(request, 'country', 100),
            'bio':                 _clean_registration_text(request, 'bio', 1200),
            'interests':           _clean_registration_text(request, 'interests', 600),
            'values':              _clean_registration_text(request, 'values', 600),
            'communication_style': _clean_registration_text(request, 'communication_style', 500),
            'relationship_goal':   _clean_registration_text(request, 'relationship_goal', 600),
            'boundaries':          _clean_registration_text(request, 'boundaries', 600),
            'social_energy':       _clean_registration_text(request, 'social_energy', 30),
        })
        request.session['reg_data'] = reg
        return redirect('/register/?step=3')

    # ── Step 3: حریم خصوصی ─────────────────────────────────────
    elif request.method == 'POST' and step == 3:
        is_public = request.POST.get('is_public', 'false') == 'true'
        reg['is_public'] = is_public
        request.session['reg_data'] = reg
        _new_captcha(request)
        return redirect('/register/?step=4')

    # ── Step 4: کپچا + ثبت نهایی ────────────────────────────────
    elif request.method == 'POST' and step == 4:
        if not _check_captcha(request):
            error = 'جواب سوال ریاضی اشتباهه.'
            _new_captcha(request)
        elif not reg.get('username'):
            return redirect('/register/?step=1')
        else:
            try:
                bd = None
                if reg.get('birth_date'):
                    try:
                        bd = date.fromisoformat(reg['birth_date'])
                    except ValueError:
                        pass

                # رمز عبور با Django signing ذخیره شده — باز می‌کنیم
                from django.core import signing
                try:
                    pw = signing.loads(reg['password_token'], salt='register-pw', max_age=3600)
                except Exception:
                    return redirect('/register/?step=1')

                user = User.objects.create_user(
                    username   = reg['username'],
                    email      = reg.get('email', ''),
                    password   = pw,
                    first_name = reg.get('first_name', ''),
                    last_name  = reg.get('last_name', ''),
                    is_public  = reg.get('is_public', False),
                    career     = reg.get('career', ''),
                    city       = reg.get('city', ''),
                    country    = reg.get('country', ''),
                    bio        = reg.get('bio', ''),
                    birth_date = bd,
                )

                # ── self-node: نود خود کاربر ──────────────────
                from main.models import Node
                self_node = Node.objects.create(
                    username   = user.username,
                    first_name = user.first_name,
                    last_name  = user.last_name,
                    career     = user.career or '',
                    birth_day  = bd,
                    name       = f'{user.first_name} {user.last_name}'.strip(),
                    owner      = user,
                    username_locked = True,   # نود خودش قفله
                )
                # ── root_node: گراف از همون اول مرکز داره ──
                user.root_node = self_node
                user.save(update_fields=['root_node'])

                # ── AI onboarding profile: private signals on the self-node ──
                profile_data = {
                    'profile_type':          'self_onboarding',
                    'about_me':              user.bio,
                    'interests':             _split_profile_items(reg.get('interests', '')),
                    'values':                _split_profile_items(reg.get('values', '')),
                    'communication_style':   reg.get('communication_style', ''),
                    'relationship_goals':    reg.get('relationship_goal', ''),
                    'boundaries':            reg.get('boundaries', ''),
                    'social_energy':         reg.get('social_energy', ''),
                }
                if any(value for key, value in profile_data.items() if key != 'profile_type'):
                    from main.models import Information
                    Information.objects.create(
                        node=self_node,
                        visibility='private',
                        data=profile_data,
                    )

                # اگه public بود، دنبال نودهای مشابه بگرد
                _trigger_new_user_sync(user)

                del request.session['reg_data']
                login(request, user)
                return redirect('/')
            except Exception as e:
                error = f'خطا در ثبت‌نام: {e}'

    captcha_q = request.session.get('captcha_question', _new_captcha(request))

    return render(request, 'auth/register.html', {
        'step':      step,
        'reg':       reg,
        'error':     error,
        'captcha_q': captcha_q,
    })


# ─────────────────────────────────────────────────────────────────
# Logout
# ─────────────────────────────────────────────────────────────────

@require_POST
def logout_view(request):
    logout(request)
    return redirect('/login/')


# ─────────────────────────────────────────────────────────────────
# Profile
# ─────────────────────────────────────────────────────────────────

@login_required
def profile_view(request):
    from main.models import Node
    user  = request.user
    error = None
    saved = False

    if request.method == 'GET':
        return redirect('profile_edit')

    if request.method == 'POST':
        action = request.POST.get('action', 'profile')

        if action == 'root_node':
            root_id = request.POST.get('root_node_id', '').strip()
            if root_id:
                try:
                    user.root_node = Node.objects.get(pk=root_id, owner=user)
                except Node.DoesNotExist:
                    error = 'نود انتخاب‌شده پیدا نشد.'
            else:
                user.root_node = None
            if not error:
                user.save()
                saved = True

        elif action == 'profile':
            user.first_name = request.POST.get('first_name', '').strip()
            user.last_name  = request.POST.get('last_name', '').strip()
            user.bio        = request.POST.get('bio', '').strip()
            user.career     = request.POST.get('career', '').strip()
            user.city       = request.POST.get('city', '').strip()
            user.country    = request.POST.get('country', '').strip()
            user.is_public  = request.POST.get('is_public', '') == 'on'
            bd_raw = request.POST.get('birth_date', '').strip()
            if bd_raw:
                try:
                    user.birth_date = date.fromisoformat(bd_raw)
                except ValueError:
                    error = 'فرمت تاریخ اشتباهه (YYYY-MM-DD).'
            if 'avatar' in request.FILES:
                user.avatar = request.FILES['avatar']
            if not error:
                user.save()
                # ── sync self-node ────────────────────────────
                try:
                    sn = Node.objects.get(username=user.username, owner=user)
                    sn.first_name    = user.first_name
                    sn.last_name     = user.last_name
                    sn.career        = user.career
                    sn.nickname      = request.POST.get('nickname', '').strip()
                    sn.phone_number  = request.POST.get('phone_number', '').strip()
                    if user.birth_date:
                        sn.birth_day = user.birth_date
                    sn.save()
                except Node.DoesNotExist:
                    pass
                # اگه public بود، به importerها notify بده
                _trigger_profile_update_sync(user)
                saved = True

        elif action == 'password':
            old_pw = request.POST.get('old_password', '')
            new_pw = request.POST.get('new_password', '')
            new_pw2= request.POST.get('new_password2', '')
            if not user.check_password(old_pw):
                error = 'رمز عبور فعلی اشتباهه.'
            elif new_pw != new_pw2:
                error = 'رمز جدید و تکرارش یکی نیستن.'
            else:
                err = _validate_password(new_pw)
                if err:
                    error = err
                else:
                    user.set_password(new_pw)
                    user.save()
                    login(request, user)
                    saved = True

    if error:
        messages.error(request, error)
    elif saved:
        messages.success(request, 'تغییرات پروفایل ذخیره شد.')
    return redirect(f'/u/{user.username}/')

    all_nodes = Node.objects.filter(owner=user).order_by('username')
    try:
        self_node = Node.objects.get(username=user.username, owner=user)
    except Node.DoesNotExist:
        self_node = None

    return render(request, 'auth/profile.html', {
        'user':      user,
        'error':     error,
        'saved':     saved,
        'all_nodes': all_nodes,
        'self_node': self_node,
    })
