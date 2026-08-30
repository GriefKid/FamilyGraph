import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db.models.signals import pre_delete
from django.dispatch import receiver


# ─────────────────────────────────────────────────────────────────
# 1. Custom User Model  (باید اول از همه باشه)
# ─────────────────────────────────────────────────────────────────

class User(AbstractUser):
    """کاربر FamilyGraph — جایگزین auth.User."""
    is_public   = models.BooleanField(
        default=False, verbose_name='حساب عمومی',
        help_text='شبکه‌ات برای دیگران قابل جستجو باشه'
    )
    trust_score = models.IntegerField(default=80, verbose_name='امتیاز اعتماد')
    avatar      = models.ImageField(upload_to='avatars/', blank=True, null=True,
                                    verbose_name='آواتار')
    cover_image = models.ImageField(upload_to='profile_covers/', blank=True, null=True,
                                    verbose_name='بک‌گراند پروفایل')
    cover_preset = models.CharField(max_length=40, blank=True, default='aurora',
                                    verbose_name='بک‌گراند آماده')
    bio         = models.TextField(blank=True, verbose_name='درباره من')
    birth_date  = models.DateField(blank=True, null=True, verbose_name='تاریخ تولد')
    career      = models.CharField(max_length=200, blank=True, verbose_name='شغل')
    city        = models.CharField(max_length=100, blank=True, verbose_name='شهر')
    country     = models.CharField(max_length=100, blank=True, verbose_name='کشور')
    # V3: نود اصلی کاربر در گراف — هر کاربر root_node خودش رو داره
    root_node   = models.ForeignKey(
        'Node', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='as_root_for', verbose_name='نود اصلی (من)',
    )
    # V12: تنظیمات شبکه اجتماعی
    discoverable           = models.BooleanField(
        default=True, verbose_name='قابل کشف',
        help_text='توی صفحه «کشف آدم‌ها» پیدا بشم (فقط اگه پابلیک باشی معنی داره)')
    auto_accept_follow     = models.BooleanField(
        default=False, verbose_name='تایید خودکار فالو',
        help_text='هر کی فالو کرد بدون تایید من قبول بشه')
    auto_accept_connection = models.BooleanField(
        default=False, verbose_name='تایید خودکار کانکشن',
        help_text='درخواست کانکشن بدون تایید من قبول بشه')
    public_interests = models.JSONField(
        default=list, blank=True, verbose_name='علایق عمومی',
        help_text='فقط برای پیشنهادهای اجتماعی و پروفایل عمومی استفاده می‌شود')
    public_values = models.JSONField(
        default=list, blank=True, verbose_name='ارزش‌های عمومی',
        help_text='فقط برای پیشنهادهای اجتماعی و پروفایل عمومی استفاده می‌شود')
    public_communication_style = models.CharField(
        max_length=280, blank=True, verbose_name='سبک ارتباط عمومی')
    CHAT_POLICY_CHOICES = [('connections', 'فقط کانکشن‌ها'), ('nobody', 'هیچ‌کس')]
    chat_policy            = models.CharField(
        max_length=12, choices=CHAT_POLICY_CHOICES, default='connections',
        verbose_name='کی می‌تونه بهم پیام بده')
    ai_extraction_enabled = models.BooleanField(default=True)
    ai_journal_enabled = models.BooleanField(default=True)
    ai_checkin_enabled = models.BooleanField(default=True)
    ai_chat_enabled = models.BooleanField(default=True)
    onboarding_completed = models.BooleanField(default=False)
    demo_mode = models.BooleanField(default=False)
    feature_overrides = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'کاربر'
        verbose_name_plural = 'کاربران'

    def __str__(self):
        return self.username

    @property
    def unread_notif_count(self):
        return self.notifications.filter(is_read=False).count()

    @property
    def pending_sync_count(self):
        """تعداد SyncNotificationهای در انتظار — برای badge sidebar."""
        return self.sync_notifications.filter(status='pending').count()

    @property
    def inbox_count(self):
        """Actionable inbox items, always scoped to this account."""
        return self.unread_notif_count + self.pending_sync_count


# ─────────────────────────────────────────────────────────────────
# 2. Group
# ─────────────────────────────────────────────────────────────────

class Group(models.Model):
    """گروه‌بندی دستی نودها — هر نود می‌تونه توی چند گروه باشه."""
    name      = models.CharField(max_length=100, verbose_name='نام گروه')
    color     = models.CharField(max_length=20, blank=True, verbose_name='رنگ',
                                  help_text='کد رنگ hex مثل #6366f1 (اختیاری)')
    owner     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='groups_owned',
        verbose_name='صاحب'
    )
    is_public = models.BooleanField(default=False, verbose_name='عمومی',
                                     help_text='این گروه برای کاربران دیگه قابل مشاهده‌ست')

    class Meta:
        ordering = ['name']
        verbose_name = 'گروه'
        verbose_name_plural = 'گروه‌ها'

    def __str__(self):
        return self.name


# ─────────────────────────────────────────────────────────────────
# 3. Node  (+ دایره نزدیکی V4)
# ─────────────────────────────────────────────────────────────────

# هر tier یه «انتظار تماس» داره — پایه‌ی امتیاز سلامت رابطه
CLOSENESS_CHOICES = [
    ('inner',        'حلقه نزدیک (هفتگی)'),
    ('close',        'نزدیک (هر ۲ هفته)'),
    ('friend',       'دوست (ماهانه)'),
    ('acquaintance', 'آشنا (هر ۳ ماه)'),
    ('distant',      'دور (بدون انتظار)'),
]

# نگاشت tier → حداکثر فاصله‌ی مورد انتظار بین دو تعامل (روز)
CLOSENESS_EXPECTED_DAYS = {
    'inner':        7,
    'close':        14,
    'friend':       30,
    'acquaintance': 90,
    'distant':      None,   # بدون انتظار — همیشه خاکستری
}

class Node(models.Model):
    username        = models.CharField(max_length=100)
    first_name      = models.CharField(max_length=100, blank=True, verbose_name='نام')
    last_name       = models.CharField(max_length=100, blank=True, verbose_name='نام خانوادگی')
    nickname        = models.CharField(max_length=100, blank=True, verbose_name='لقب / اسم مستعار')
    picture         = models.ImageField(upload_to='media/', blank=True, null=True)
    name            = models.CharField(max_length=200, blank=True)   # legacy
    birth_day       = models.DateField(blank=True, null=True)
    career          = models.CharField(max_length=200, blank=True)
    phone_number    = models.CharField(max_length=20, blank=True)
    group           = models.CharField(max_length=100, blank=True,
                                        verbose_name='گروه (قدیمی)',
                                        help_text='legacy — از groups استفاده کن')
    groups          = models.ManyToManyField(Group, blank=True, related_name='nodes',
                                              verbose_name='گروه‌ها')
    # V3: multi-tenancy
    owner           = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='nodes',
        verbose_name='صاحب'
    )
    # V3: این نود در پروفایل عمومی قابل مشاهده‌ست
    is_public       = models.BooleanField(
        default=False, verbose_name='عمومی',
        help_text='اگه حساب عمومی باشه، این نود برای دیگران قابل مشاهده‌ست'
    )
    # V3: یوزرنیمی که از public import شده قابل تغییر نیست
    username_locked = models.BooleanField(default=False,
                                           verbose_name='یوزرنیم قفل‌شده',
                                           help_text='نودی که از حساب عمومی import شده')
    imported_from   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='exported_nodes',
        verbose_name='وارد شده از'
    )
    merged_into = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='merged_duplicates')
    is_pinned = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True, null=True)
    is_demo = models.BooleanField(default=False)

    def display_name(self):
        if self.nickname:
            return self.nickname
        full = f"{self.first_name} {self.last_name}".strip()
        if full:
            return full
        return self.name or self.username

    def __str__(self):
        return self.username or f"Node {self.pk}"

    class Meta:
        ordering = ['username']
        indexes = [
            models.Index(fields=['owner', 'username'], name='node_owner_username'),
            models.Index(fields=['owner', 'merged_into', 'is_pinned'], name='node_owner_merge_pin'),
        ]


# ─────────────────────────────────────────────────────────────────
# 4. Relationship
# ─────────────────────────────────────────────────────────────────


class Relationship(models.Model):
    STATUS_CHOICES = [
        ('active',   'فعال'),
        ('distant',  'دور شده'),
        ('inactive', 'غیرفعال'),
    ]

    rel      = models.CharField(max_length=100, blank=True, null=True)
    source   = models.ForeignKey(Node, related_name='as_source', on_delete=models.PROTECT)
    target   = models.ForeignKey(Node, related_name='as_target', on_delete=models.PROTECT)
    strength = models.IntegerField(default=3, choices=[(i, i) for i in range(1, 6)])
    status   = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    met_at    = models.DateField(blank=True, null=True, verbose_name='تاریخ آشنایی')
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    is_public = models.BooleanField(
        default=False, verbose_name='عمومی',
        help_text='اگه حساب عمومی باشه، این رابطه برای دیگران قابل مشاهده‌ست'
    )
    owner    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='relationships',
        verbose_name='صاحب'
    )

    def __str__(self):
        return self.rel or f"{self.source} → {self.target}"

    def emoji(self):
        """ایموجی متناسب با نوع رابطه — نه همه‌چیز قلب! (V11)"""
        t = (self.rel or '').lower()
        if any(w in t for w in ('همسر', 'عشق', 'نامزد', 'زن', 'شوهر', 'love')):
            return '❤️'
        if any(w in t for w in ('پدر', 'مادر', 'مامان', 'بابا', 'برادر', 'خواهر', 'داداش',
                                'آبجی', 'عمو', 'دایی', 'خاله', 'عمه', 'پسر', 'دختر',
                                'فامیل', 'خانواده', 'پدربزرگ', 'مادربزرگ')):
            return '🏠'
        if any(w in t for w in ('همکار', 'کار', 'رئیس', 'مدیر', 'کارفرما', 'شریک',
                                'استاد', 'شاگرد', 'مشاور', 'business')):
            return '💼'
        if any(w in t for w in ('دوست', 'رفیق', 'صمیمی')):
            return '💛'
        if any(w in t for w in ('همسایه',)):
            return '🏘'
        if any(w in t for w in ('تلگرام', 'familygraph', 'آنلاین')):
            return '🔗'
        return '💞'

    def clean(self):
        if self.source == self.target:
            raise ValidationError("A node cannot have a relationship with itself.")
        if self.owner_id:
            for node in (self.source, self.target):
                if node.owner_id and node.owner_id != self.owner_id:
                    raise ValidationError('Relationship endpoints must belong to the same owner.')

    def save(self, *args, **kwargs):
        # جلوگیری از self-loop حتی در create برنامه‌نویسی (clean فقط در form صدا زده میشه)
        if self.source_id == self.target_id:
            raise ValidationError("A node cannot have a relationship with itself.")
        self.clean()
        # تشخیص تغییر strength برای ثبت تاریخچه
        if self.pk:
            try:
                old = Relationship.objects.get(pk=self.pk)
                self._strength_changed = (old.strength != self.strength)
            except Relationship.DoesNotExist:
                self._strength_changed = True
        else:
            self._strength_changed = True  # اولین ذخیره
        super().save(*args, **kwargs)
        if getattr(self, '_strength_changed', False):
            RelationshipStrengthHistory.objects.create(
                relationship=self,
                strength=self.strength,
                owner=self.owner,
            )

    class Meta:
        unique_together = ('source', 'target', 'rel')
        indexes = [
            models.Index(fields=['owner', 'status', '-strength'], name='rel_owner_status_strength'),
            models.Index(fields=['owner', 'source'], name='rel_owner_source'),
            models.Index(fields=['owner', 'target'], name='rel_owner_target'),
        ]


# ─────────────────────────────────────────────────────────────────
# 5. Event
# ─────────────────────────────────────────────────────────────────

class Event(models.Model):
    title        = models.CharField(max_length=200)
    date         = models.DateField()
    event_time   = models.TimeField(null=True, blank=True, verbose_name='ساعت')
    description  = models.TextField(blank=True)
    participants = models.ManyToManyField(Node, blank=True, related_name='events')
    owner        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='events',
        verbose_name='صاحب'
    )
    # reminder tracking
    reminder_sent_7d  = models.BooleanField(default=False)
    reminder_sent_1d  = models.BooleanField(default=False)
    reminder_sent_3h  = models.BooleanField(default=False)
    # post-event journal prompt
    post_event_prompted = models.BooleanField(default=False)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['date']  # event_time جداگانه در view مدیریت می‌شه
        indexes = [
            models.Index(fields=['owner', 'date'], name='event_owner_date'),
        ]



# ─────────────────────────────────────────────────────────────────
# 6. Information
# ─────────────────────────────────────────────────────────────────

class Information(models.Model):
    VISIBILITY_CHOICES = [
        ('public',  'Everyone can see'),
        ('friends', 'Friends can see'),
        ('selected', 'Selected friends can see'),
        ('shared',  'Someone can see'),
        ('private', 'Nobody'),
    ]

    node = models.ForeignKey(
        Node, on_delete=models.PROTECT, related_name='informations'
    )
    visibility = models.CharField(
        max_length=10, choices=VISIBILITY_CHOICES, default='private'
    )
    data = models.JSONField(blank=True, null=True)
    shared_with = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='shared_informations',
        verbose_name='Shared with selected friends'
    )

    def __str__(self):
        return f'Information #{self.id} - {self.node}'


class Friendship(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='friendships')
    friend = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='friend_of')
    relationship = models.ForeignKey(Relationship, on_delete=models.SET_NULL, null=True, blank=True, related_name='friendships')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'friend')
        ordering = ['friend__username']

    def __str__(self):
        return f'{self.user} ↔ {self.friend}'


class Follow(models.Model):
    follower = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='following')
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='followers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('follower', 'target')
        ordering = ['target__username']

    def __str__(self):
        return f'{self.follower} follows {self.target}'


class FriendRequest(models.Model):
    REQUEST_TYPES = [
        ('follow', 'Follow'),
        ('connection', 'Connection'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_friend_requests')
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_friend_requests')
    request_type = models.CharField(max_length=12, choices=REQUEST_TYPES, default='connection')
    message = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('sender', 'receiver', 'request_type', 'status')

    def __str__(self):
        return f'{self.sender} → {self.receiver} ({self.status})'


class DirectMessage(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_direct_messages')
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_direct_messages')
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    reply_to = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies')
    analyzed = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.sender} → {self.receiver}: {self.content[:60]}'


class ChatAnalysis(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chat_analyses')
    friend = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chat_analysed_by')
    summary = models.TextField(blank=True)
    mood = models.CharField(max_length=120, blank=True)
    topics = models.JSONField(default=list, blank=True)
    signals = models.JSONField(default=list, blank=True)
    suggestions = models.JSONField(default=list, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'friend')
        ordering = ['-updated_at']

    def __str__(self):
        return f'Chat analysis: {self.user} / {self.friend}'


class ArtisticWork(models.Model):
    KIND_CHOICES = [
        ('book', 'Book'),
        ('movie', 'Movie'),
        ('series', 'Series'),
        ('music', 'Music'),
    ]
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    title = models.CharField(max_length=240)
    creator = models.CharField(max_length=180, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    genres = models.JSONField(default=list, blank=True)
    analysis = models.JSONField(default=dict, blank=True)
    cover_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['title']
        unique_together = ('kind', 'title')

    def save(self, *args, **kwargs):
        if not self.analysis:
            self.analysis = {
                'personality_signals': [],
                'relationship_signals': [],
                'summary': 'این اثر برای شناخت سلیقه، ارزش‌ها و جهان ذهنی فرد استفاده می‌شود.',
            }
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_kind_display()}: {self.title}'


class ProfileMediaItem(models.Model):
    KIND_CHOICES = ArtisticWork.KIND_CHOICES
    SOURCE_CHOICES = [
        ('manual', 'Manual'),
        ('journal', 'Journal'),
        ('imported', 'Imported'),
    ]
    STATUS_CHOICES = [
        ('completed', 'تمام‌شده'),
        ('current', 'در حال خواندن / دیدن / گوش دادن'),
        ('planned', 'در برنامه'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile_media_items')
    work = models.ForeignKey(ArtisticWork, null=True, blank=True, on_delete=models.SET_NULL, related_name='user_items')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    title = models.CharField(max_length=240)
    creator = models.CharField(max_length=180, blank=True)
    rating = models.FloatField(default=0)
    completed_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='completed')
    is_public = models.BooleanField(
        default=True,
        verbose_name='نمایش در پروفایل عمومی',
        help_text='فالوئرها و بازدیدکنندگان فقط آثار عمومی را می‌بینند',
    )
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, default='manual')
    source_journal = models.ForeignKey('JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='detected_media_items')
    notes = models.TextField(blank=True)
    analysis = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-completed_on', '-created_at']
        unique_together = ('user', 'kind', 'title')

    def save(self, *args, **kwargs):
        try:
            self.rating = max(0, min(5, float(self.rating or 0)))
        except Exception:
            self.rating = 0
        if not self.analysis:
            direction = 'همسو' if self.rating >= 3.5 else ('برخلاف سلیقه' if self.rating <= 2 else 'خنثی/در حال کشف')
            work_summary = self.work.analysis.get('summary', '') if self.work_id and isinstance(self.work.analysis, dict) else ''
            self.analysis = {
                'stance': direction,
                'signal': f'امتیاز {self.rating:g}/5 نشان می‌دهد این اثر برای شناخت سلیقه فرد {direction} است.',
                'work_summary': work_summary,
            }
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.user} {self.kind}: {self.title}'


class SocialPost(models.Model):
    """A deliberately small public post: the social feed must never expose private notes."""

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='social_posts'
    )
    body = models.TextField(max_length=1200)
    image = models.ImageField(upload_to='social_posts/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_public = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'پست اجتماعی'
        verbose_name_plural = 'پست‌های اجتماعی'

    def __str__(self):
        return f'{self.author.username}: {self.body[:60]}'


class SocialCircle(models.Model):
    """A private group that can only contain mutually connected users."""

    name = models.CharField(max_length=100)
    description = models.CharField(max_length=280, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='social_circles_created'
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='social_circles', blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'حلقه اجتماعی'
        verbose_name_plural = 'حلقه‌های اجتماعی'

    def __str__(self):
        return self.name


class SocialCircleMessage(models.Model):
    circle = models.ForeignKey(
        SocialCircle, on_delete=models.CASCADE, related_name='messages'
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='social_circle_messages'
    )
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'پیام حلقه اجتماعی'
        verbose_name_plural = 'پیام‌های حلقه اجتماعی'

    def __str__(self):
        return f'{self.circle.name}: {self.body[:60]}'


# ─────────────────────────────────────────────────────────────────
# 4b. RelationshipStrengthHistory  (V3 — تاریخچه قدرت رابطه)
# ─────────────────────────────────────────────────────────────────

class RelationshipStrengthHistory(models.Model):
    """تاریخچه تغییر قدرت رابطه — برای نمودار روند."""
    relationship = models.ForeignKey(
        Relationship, on_delete=models.CASCADE,
        related_name='strength_history', verbose_name='رابطه'
    )
    strength   = models.IntegerField(verbose_name='قدرت')
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name='زمان تغییر')
    note       = models.CharField(max_length=200, blank=True, verbose_name='یادداشت')
    owner      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='strength_histories',
        verbose_name='صاحب'
    )

    class Meta:
        ordering = ['-changed_at']
        verbose_name = 'تاریخچه قدرت'
        verbose_name_plural = 'تاریخچه قدرت‌ها'

    def __str__(self):
        return f"{self.relationship} → {self.strength}"


# ─────────────────────────────────────────────────────────────────
# 7. AppSettings
# ─────────────────────────────────────────────────────────────────

class AppSettings(models.Model):
    """
    DEPRECATED: root_node به مدل User منتقل شده (V3).
    این مدل فقط برای backward-compat نگه داشته شده.
    """

    def __str__(self):
        return "AppSettings (deprecated — از user.root_node استفاده کن)"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    class Meta:
        verbose_name = 'تنظیمات (قدیمی)'
        verbose_name_plural = 'تنظیمات (قدیمی)'


# ─────────────────────────────────────────────────────────────────
# 8. JournalEntry
# ─────────────────────────────────────────────────────────────────

class JournalEntry(models.Model):
    ENTRY_KIND_CHOICES = [
        ('moment', 'لحظه'),
        ('reflection', 'جمع‌بندی روز'),
    ]
    text            = models.TextField(verbose_name='متن')
    entry_date      = models.DateField(null=True, blank=True, verbose_name='تاریخ رویداد')
    occurred_at     = models.DateTimeField(null=True, blank=True, verbose_name='زمان رخداد')
    entry_kind      = models.CharField(max_length=12, choices=ENTRY_KIND_CHOICES,
                                       default='reflection', verbose_name='نوع ثبت')
    tags            = models.JSONField(default=list, blank=True, verbose_name='تگ‌ها')
    mood            = models.CharField(max_length=100, blank=True, verbose_name='خلق‌وخو')
    ai_analyzed     = models.BooleanField(default=False, verbose_name='آنالیز AI')
    mentioned_nodes = models.ManyToManyField('Node', blank=True, related_name='journal_entries')
    created_at      = models.DateTimeField(auto_now_add=True)
    owner           = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='journal_entries',
        verbose_name='صاحب'
    )

    def __str__(self):
        return f"یادداشت {self.created_at.date()}: {self.text[:50]}"

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['owner', '-created_at'], name='journal_owner_created'),
                   models.Index(fields=['owner', 'entry_date'], name='journal_owner_date')]



class RelationshipPulse(models.Model):
    """Optional, private self-report used by relationship theory monitors."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='relationship_pulses')
    node = models.ForeignKey('Node', null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='relationship_pulses')
    support = models.PositiveSmallIntegerField(default=3)      # felt support
    autonomy = models.PositiveSmallIntegerField(default=3)     # ability to be oneself
    belonging = models.PositiveSmallIntegerField(default=3)    # felt connection
    trust = models.PositiveSmallIntegerField(default=3)        # safety / trust
    voice = models.PositiveSmallIntegerField(default=3)        # can disagree respectfully
    note = models.CharField(max_length=280, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        from django.core.exceptions import ValidationError
        for field in ('support', 'autonomy', 'belonging', 'trust', 'voice'):
            if not 1 <= getattr(self, field) <= 5:
                raise ValidationError({field: 'امتیاز باید بین ۱ تا ۵ باشد.'})


    def save(self, *args, **kwargs):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Relationship pulse and node must belong to the same owner.')
        self.clean()
        super().save(*args, **kwargs)


class ExtractionSuggestion(models.Model):
    """Private, user-approved facts extracted from any text entry point."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='extraction_suggestions')
    source = models.CharField(max_length=20)  # journal, checkin, chat
    source_id = models.PositiveIntegerField(null=True, blank=True)
    kind = models.CharField(max_length=20)    # event, debt, person, relationship, signal
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default='pending')
    fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    applied_model = models.CharField(max_length=30, blank=True)
    applied_object_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['owner', 'status', '-created_at'], name='extract_owner_status')]
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'source', 'source_id', 'fingerprint'],
                condition=~models.Q(fingerprint=''),
                name='unique_extraction_per_source',
            ),
        ]


class NodeAlias(models.Model):
    """A user-confirmed way of referring to a person (name, nickname or role)."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='node_aliases')
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='aliases')
    alias = models.CharField(max_length=100)
    normalized_alias = models.CharField(max_length=100, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner', 'normalized_alias'],
                                                name='unique_node_alias_per_owner')]

    def save(self, *args, **kwargs):
        if self.owner_id and self.node_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Alias and node must belong to the same owner.')
        self.normalized_alias = ' '.join(self.alias.replace('ي', 'ی').replace('ك', 'ک').lower().split())
        super().save(*args, **kwargs)


class MemoryFact(models.Model):
    CATEGORY_CHOICES = [
        ('interest', 'علاقه'), ('value', 'ارزش'), ('communication', 'سبک ارتباطی'),
        ('boundary', 'مرز'), ('sensitivity', 'حساسیت'), ('preference', 'ترجیح'),
        ('life_topic', 'موضوع زندگی'), ('emotion', 'محرک احساسی'), ('other', 'سایر'),
    ]
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='memory_facts')
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='memory_facts')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    value = models.CharField(max_length=300)
    confidence = models.PositiveSmallIntegerField(default=70)
    source = models.CharField(max_length=20)
    source_id = models.PositiveIntegerField(null=True, blank=True)
    suggestion = models.ForeignKey(ExtractionSuggestion, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='memory_facts')
    observed_at = models.DateTimeField(default=models.functions.Now)
    active = models.BooleanField(default=True)
    ai_usable = models.BooleanField(default=True)
    confidentiality = models.CharField(max_length=15, default='personal', choices=[
        ('normal', 'معمولی'), ('personal', 'شخصی'), ('sensitive', 'بسیار حساس'),
        ('financial', 'مالی'), ('health', 'سلامت'), ('no_ai', 'ممنوع برای AI')])
    superseded_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='superseded_facts')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category', '-confidence', '-observed_at']
        indexes = [models.Index(fields=['owner', 'node', 'active'], name='memory_owner_node_active'),
                   models.Index(fields=['owner', 'active'], name='memory_owner_active')]
        constraints = [models.UniqueConstraint(fields=['owner', 'node', 'category', 'value'],
                                                name='unique_memory_fact_per_node')]

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Memory fact and node must belong to the same owner.')
        if not 0 <= self.confidence <= 100:
            raise ValidationError({'confidence': 'Confidence must be between 0 and 100.'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def effective_confidence(self):
        """Confidence slowly decays, while direct/manual knowledge stays stronger."""
        from django.utils import timezone
        age_days = max(0, (timezone.now() - self.observed_at).days)
        decay = min(30, age_days // 90 * 3)
        source_bonus = 8 if self.source == 'manual' else 0
        conflict_penalty = 12 if self.superseded_by_id else 0
        return min(100, max(0, self.confidence + source_bonus - decay - conflict_penalty))


class RelationshipRecommendation(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='relationship_recommendations')
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='recommendations')
    kind = models.CharField(max_length=30, default='connect')
    title = models.CharField(max_length=200)
    suggestion = models.TextField()
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=15, default='active')
    snoozed_until = models.DateField(null=True, blank=True)
    outcome = models.CharField(max_length=20, blank=True)
    outcome_note = models.CharField(max_length=300, blank=True)
    helpful = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    acted_at = models.DateTimeField(null=True, blank=True)

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Recommendation and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-created_at']


class NodeMergeOperation(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='node_merge_operations')
    primary_node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='merge_operations_primary')
    duplicate_node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='merge_operations_duplicate')
    snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default='applied')
    created_at = models.DateTimeField(auto_now_add=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    def clean(self):
        for node in (self.primary_node, self.duplicate_node):
            if self.owner_id and node.owner_id and node.owner_id != self.owner_id:
                raise ValidationError('Merge nodes must belong to the same owner.')
        if self.primary_node_id and self.duplicate_node_id and self.primary_node_id == self.duplicate_node_id:
            raise ValidationError('A node cannot be merged into itself.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class Commitment(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='commitments')
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='commitments')
    responsible = models.CharField(max_length=10, choices=[('me', 'من'), ('them', 'طرف مقابل')])
    text = models.CharField(max_length=300)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, default='open')
    source = models.CharField(max_length=20, default='manual')
    source_id = models.PositiveIntegerField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Commitment and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['status', 'due_date', '-created_at']


class GiftIdea(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='gift_ideas')
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='gift_ideas')
    title = models.CharField(max_length=200)
    occasion = models.CharField(max_length=100, blank=True)
    budget = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=15, default='idea')
    notes = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Gift idea and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['status', '-created_at']


class MeetingReflection(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='meeting_reflections')
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='meeting_reflections')
    event = models.ForeignKey(Event, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name='reflections')
    happened_at = models.DateTimeField(default=models.functions.Now)
    summary = models.TextField()
    feeling = models.SmallIntegerField(default=0)
    relationship_change = models.CharField(max_length=10, default='same')
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Reflection and node must belong to the same owner.')
        if self.owner_id and self.event_id and self.event.owner_id and self.event.owner_id != self.owner_id:
            raise ValidationError('Reflection and event must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class NodeSafetySetting(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='node_safety_settings')
    node = models.OneToOneField(Node, on_delete=models.CASCADE, related_name='safety_setting')
    pause_contact_suggestions = models.BooleanField(default=False)
    no_contact_until = models.DateField(null=True, blank=True)
    hide_emotional_reminders = models.BooleanField(default=False)
    boundaries = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Safety setting and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class FeatureFlag(models.Model):
    name = models.SlugField(max_length=80, unique=True)
    label = models.CharField(max_length=160)
    enabled = models.BooleanField(default=False)
    staff_only = models.BooleanField(default=False)
    rollout_percent = models.PositiveSmallIntegerField(default=100)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_enabled_for(self, user):
        override = (getattr(user, 'feature_overrides', None) or {}).get(self.name)
        if isinstance(override, bool):
            return override and not (self.staff_only and not getattr(user, 'is_staff', False))
        if not self.enabled or (self.staff_only and not getattr(user, 'is_staff', False)):
            return False
        return (getattr(user, 'pk', 0) or 0) % 100 < min(100, self.rollout_percent)


class AIExtractionTrace(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.CASCADE, related_name='ai_extraction_traces')
    source = models.CharField(max_length=20)
    source_id = models.PositiveIntegerField(null=True, blank=True)
    input_text = models.TextField(blank=True)
    regex_output = models.JSONField(default=list)
    ai_output = models.JSONField(default=list)
    merged_output = models.JSONField(default=list)
    provider = models.CharField(max_length=40, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, default='regex_only')
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['owner', '-created_at'], name='ai_trace_owner_created')]


class KnowledgeTriple(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='knowledge_triples')
    subject = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='knowledge_subjects')
    predicate = models.CharField(max_length=80)
    object_text = models.CharField(max_length=300, blank=True)
    object_node = models.ForeignKey(Node, null=True, blank=True, on_delete=models.CASCADE,
                                    related_name='knowledge_objects')
    confidence = models.PositiveSmallIntegerField(default=70)
    source = models.CharField(max_length=20)
    source_id = models.PositiveIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        for node in (self.subject, self.object_node):
            if self.owner_id and node and node.owner_id and node.owner_id != self.owner_id:
                raise ValidationError('Knowledge nodes must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        indexes = [models.Index(fields=['owner', 'predicate'], name='knowledge_owner_pred')]
        constraints = [models.UniqueConstraint(
            fields=['owner', 'subject', 'predicate', 'object_text', 'object_node'],
            name='unique_knowledge_triple')]


class ObservabilityEvent(models.Model):
    request_id = models.CharField(max_length=36, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='observability_events')
    level = models.CharField(max_length=10, default='error')
    area = models.CharField(max_length=40, default='backend')
    path = models.CharField(max_length=240, blank=True)
    code = models.CharField(max_length=100, blank=True)
    message = models.CharField(max_length=500)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['area', '-created_at'], name='obs_area_created')]


# ─────────────────────────────────────────────────────────────────
# 8a. NodeCloseness  (V4 — دایره نزدیکی)
# ─────────────────────────────────────────────────────────────────
# عمداً جدول جداست (نه ستون روی Node) تا اگه migrate نشده بود،
# هیچ صفحه‌ی موجودی نشکنه — فقط فیچرهای V4 با fallback کار می‌کنن.

class NodeCloseness(models.Model):
    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Closeness setting and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    node  = models.OneToOneField(Node, on_delete=models.CASCADE,
                                  related_name='closeness_setting', verbose_name='شخص')
    tier  = models.CharField(max_length=15, choices=CLOSENESS_CHOICES,
                              verbose_name='دایره نزدیکی')
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='closeness_settings',
        verbose_name='صاحب'
    )

    class Meta:
        verbose_name = 'دایره نزدیکی'
        verbose_name_plural = 'دایره‌های نزدیکی'

    def __str__(self):
        return f"{self.node} — {self.get_tier_display()}"


# ─────────────────────────────────────────────────────────────────
# 8b. Interaction  (V4 — ثبت سریع تعامل)
# ─────────────────────────────────────────────────────────────────

class Interaction(models.Model):
    """یه تماس/دیدار/پیام با یک نفر — خوراک اصلی امتیاز سلامت رابطه."""
    KIND_CHOICES = [
        ('call',    '📞 تلفنی'),
        ('meet',    '🤝 حضوری'),
        ('message', '💬 پیام'),
        ('online',  '🌐 آنلاین'),
        ('journal', '📓 از ژورنال'),
        ('checkin', '⚡ چک-این'),
        ('other',   '✦ سایر'),
    ]
    FEELING_CHOICES = [
        (1,  '😊 خوب'),
        (0,  '😐 معمولی'),
        (-1, '😕 ناخوشایند'),
    ]

    node       = models.ForeignKey(Node, on_delete=models.CASCADE,
                                    related_name='interactions', verbose_name='شخص')
    kind       = models.CharField(max_length=15, choices=KIND_CHOICES,
                                   default='call', verbose_name='نوع')
    date       = models.DateField(verbose_name='تاریخ')
    feeling    = models.SmallIntegerField(choices=FEELING_CHOICES, default=0,
                                           verbose_name='حس بعدش')
    note       = models.CharField(max_length=300, blank=True, default='',
                                   verbose_name='یادداشت کوتاه')
    created_at = models.DateTimeField(auto_now_add=True)
    owner      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='interactions',
        verbose_name='صاحب'
    )

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'تعامل'
        verbose_name_plural = 'تعامل‌ها'
        indexes = [
            models.Index(fields=['owner', 'node', '-date'], name='ix_inter_owner_node_date'),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} با {self.node} — {self.date}"

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Interaction and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────
# 8c. FollowUp  (V4 — موضوعات باز / قول‌ها)
# ─────────────────────────────────────────────────────────────────

class FollowUp(models.Model):
    """یه موضوع باز با یک نفر — قول، سوال، کاری که باید انجام بشه."""
    node       = models.ForeignKey(Node, on_delete=models.CASCADE,
                                    related_name='followups', verbose_name='شخص')
    text       = models.CharField(max_length=300, verbose_name='موضوع')
    due_date   = models.DateField(null=True, blank=True, verbose_name='سررسید')
    done       = models.BooleanField(default=False, verbose_name='انجام شد')
    done_at    = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    owner      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='followups',
        verbose_name='صاحب'
    )

    class Meta:
        ordering = ['done', 'due_date', '-created_at']
        indexes = [
            models.Index(fields=['owner', 'done', 'due_date'], name='follow_owner_done_due'),
        ]
        verbose_name = 'موضوع باز'
        verbose_name_plural = 'موضوعات باز'

    def __str__(self):
        return f"{self.text[:50]} — {self.node}"

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Follow-up and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────
# 8d. Debt  (V6 — دفتر قرض و طلب)
# ─────────────────────────────────────────────────────────────────

class Debt(models.Model):
    """قرض/طلب با یک نفر — واحد مبادله‌ی مالی رابطه."""
    DIRECTION_CHOICES = [
        ('i_owe',    'من بدهکارم'),
        ('they_owe', 'من طلبکارم'),
    ]
    CURRENCY_CHOICES = [
        ('تومان', 'تومان'),
        ('دلار',  'دلار'),
        ('یورو',  'یورو'),
    ]

    node       = models.ForeignKey(Node, on_delete=models.CASCADE,
                                    related_name='debts', verbose_name='طرف حساب')
    direction  = models.CharField(max_length=10, choices=DIRECTION_CHOICES,
                                   verbose_name='جهت')
    amount     = models.BigIntegerField(verbose_name='مبلغ')
    paid       = models.BigIntegerField(default=0, verbose_name='پرداخت‌شده')
    currency   = models.CharField(max_length=20, choices=CURRENCY_CHOICES,
                                   default='تومان', verbose_name='واحد')
    date       = models.DateField(verbose_name='تاریخ قرض')
    due_date   = models.DateField(null=True, blank=True, verbose_name='سررسید')
    note       = models.CharField(max_length=300, blank=True, default='',
                                   verbose_name='بابت')
    settled    = models.BooleanField(default=False, verbose_name='تسویه شد')
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    owner      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='debts',
        verbose_name='صاحب'
    )

    class Meta:
        ordering = ['settled', 'due_date', '-created_at']
        verbose_name = 'قرض/طلب'
        verbose_name_plural = 'قرض و طلب‌ها'

    @property
    def remaining(self):
        return max(0, (self.amount or 0) - (self.paid or 0))

    def __str__(self):
        return f"{self.get_direction_display()} {self.amount:,} {self.currency} — {self.node}"

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Debt and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────
# 8e. ChatMessage  (V8 — حافظه‌ی همدم)
# ─────────────────────────────────────────────────────────────────

class ChatMessage(models.Model):
    """پیام‌های چت با همدم — تا گفتگو بین جلسه‌ها یادش بمونه."""
    ROLE_CHOICES = [('user', 'کاربر'), ('assistant', 'همدم')]

    role       = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    owner      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='chat_messages',
        verbose_name='صاحب'
    )

    class Meta:
        ordering = ['created_at']
        verbose_name = 'پیام چت'
        verbose_name_plural = 'پیام‌های چت'

    def __str__(self):
        return f"{self.role}: {self.content[:60]}"


# ─────────────────────────────────────────────────────────────────
# 8f. LifeEvent  (V10 — رویدادهای زندگی + آیین پیگیری)
# ─────────────────────────────────────────────────────────────────

# هر نوع رویداد یه «آیین پیگیری» داره: (فاصله از روز رویداد، چی‌کار کن)
LIFE_EVENT_RITUALS = {
    'mourning': [(0, 'تسلیت بگو — همین امروز'), (3, 'روز سوم — سر بزن یا زنگ بزن'),
                 (7, 'روز هفتم — حضورت مهمه'), (40, 'چهلم — حتماً سر بزن'),
                 (365, 'سالگرد — یادش کن، یادشون نره که یادته')],
    'illness':  [(0, 'عیادت / پیگیری حالش'), (3, 'حالشو بپرس'), (14, 'پیگیری روند بهبودی')],
    'wedding':  [(0, 'تبریک بگو! 🎉'), (7, 'بپرس مراسم/ماه عسل چطور بود')],
    'baby':     [(0, 'تبریک! 🍼'), (14, 'بپرس شب‌ها چطور می‌گذره 😄'), (40, 'سر بزن')],
    'exam':     [(-1, 'شب قبل — آرزوی موفقیت کن'), (0, 'روز امتحان — بهش فکر می‌کنی، بگو'),
                 (21, 'نتیجه رو بپرس')],
    'job':      [(0, 'تبریک شغل/موقعیت جدید 💼'), (30, 'بپرس محیط جدید چطوره')],
    'move':     [(0, 'خسته نباشید بگو 📦'), (7, 'سر بزن — کمکی لازم داره؟')],
    'other':    [(0, 'پیگیری کن')],
}


class LifeEvent(models.Model):
    """رویداد مهم زندگی یک شخص — سوگ، جراحی، کنکور، عروسی…"""
    KIND_CHOICES = [
        ('mourning', '🖤 سوگ / فوت عزیز'),
        ('illness',  '🏥 بیماری / جراحی'),
        ('wedding',  '💍 ازدواج'),
        ('baby',     '🍼 تولد فرزند'),
        ('exam',     '📝 امتحان / کنکور'),
        ('job',      '💼 شغل / موقعیت جدید'),
        ('move',     '📦 اسباب‌کشی / مهاجرت'),
        ('other',    '✦ سایر'),
    ]

    node       = models.ForeignKey(Node, on_delete=models.CASCADE,
                                    related_name='life_events', verbose_name='شخص')
    kind       = models.CharField(max_length=15, choices=KIND_CHOICES, verbose_name='نوع')
    title      = models.CharField(max_length=200, blank=True, default='',
                                   verbose_name='توضیح کوتاه')
    date       = models.DateField(verbose_name='تاریخ رویداد')
    archived   = models.BooleanField(default=False, verbose_name='بایگانی')
    created_at = models.DateTimeField(auto_now_add=True)
    owner      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='life_events', verbose_name='صاحب')

    class Meta:
        ordering = ['-date']
        verbose_name = 'رویداد زندگی'
        verbose_name_plural = 'رویدادهای زندگی'

    def __str__(self):
        return f"{self.get_kind_display()} — {self.node} ({self.date})"

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Life event and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────
# 8g. RelationshipGoal  (V10 — هدف روی رابطه)
# ─────────────────────────────────────────────────────────────────

class RelationshipGoal(models.Model):
    """«می‌خوام با X صمیمی‌تر شم» — هدف + سنجش پیشرفت."""
    STATUS_CHOICES = [
        ('active',    'در جریان'),
        ('achieved',  'رسیدم! 🎉'),
        ('abandoned', 'بی‌خیالش'),
    ]

    node            = models.ForeignKey(Node, on_delete=models.CASCADE,
                                         related_name='goals', verbose_name='شخص')
    text            = models.CharField(max_length=300, verbose_name='هدف')
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES,
                                        default='active')
    baseline_score  = models.IntegerField(null=True, blank=True,
                                           verbose_name='امتیاز سلامت شروع')
    created_at      = models.DateTimeField(auto_now_add=True)
    closed_at       = models.DateTimeField(null=True, blank=True)
    owner           = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='relationship_goals', verbose_name='صاحب')

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Relationship goal and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['status', '-created_at']
        verbose_name = 'هدف رابطه'
        verbose_name_plural = 'اهداف رابطه'

    def __str__(self):
        return f"{self.text[:50]} — {self.node}"


# ─────────────────────────────────────────────────────────────────
# 8h. شناخت (V11) — جمع‌بندی کلی اپ از یک شخص یا یک رابطه
# ─────────────────────────────────────────────────────────────────

class PersonaProfile(models.Model):
    """هر چیزی که اپ تا حالا از یک شخص فهمیده — جملات کلی، بدون ذکر منبع."""
    node       = models.OneToOneField(Node, on_delete=models.CASCADE,
                                       related_name='persona', verbose_name='شخص')
    summary    = models.TextField(blank=True, default='', verbose_name='جمع‌بندی')
    statements = models.JSONField(default=list, blank=True,
                                   verbose_name='جملات شناخت')
    previous_statements = models.JSONField(default=list, blank=True,
                                           verbose_name='جملات نسخهٔ قبل')
    previous_synth_at = models.DateTimeField(null=True, blank=True,
                                             verbose_name='زمان سنتز قبلی')
    updated_at = models.DateTimeField(auto_now=True)
    owner      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='personas', verbose_name='صاحب')

    def clean(self):
        if self.owner_id and self.node_id and self.node.owner_id and self.node.owner_id != self.owner_id:
            raise ValidationError('Persona and node must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'شناخت شخص'
        verbose_name_plural = 'شناخت اشخاص'

    def __str__(self):
        return f'شناخت {self.node}'


class RelationshipProfile(models.Model):
    """جمع‌بندی کلی اپ از یک رابطه (یال)."""
    relationship = models.OneToOneField(Relationship, on_delete=models.CASCADE,
                                         related_name='profile', verbose_name='رابطه')
    summary      = models.TextField(blank=True, default='')
    statements   = models.JSONField(default=list, blank=True)
    previous_statements = models.JSONField(default=list, blank=True)
    previous_synth_at = models.DateTimeField(null=True, blank=True)
    updated_at   = models.DateTimeField(auto_now=True)
    owner        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='relationship_profiles', verbose_name='صاحب')

    def clean(self):
        if self.owner_id and self.relationship_id and self.relationship.owner_id and self.relationship.owner_id != self.owner_id:
            raise ValidationError('Relationship profile and relationship must belong to the same owner.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'شناخت رابطه'
        verbose_name_plural = 'شناخت روابط'

    def __str__(self):
        return f'شناخت {self.relationship}'


# ─────────────────────────────────────────────────────────────────
# 8i. SharedItem  (V12 — اشتراک‌گذاری راس/یال/دیتا با فالوئرها)
# ─────────────────────────────────────────────────────────────────

class SharedItem(models.Model):
    """رکورد هر چیزی که برای یک فالوئر شیر شده — راس، یال یا اطلاعات."""
    ITEM_TYPES = [
        ('node', '👤 راس'),
        ('edge', '💞 یال'),
        ('info', '💡 اطلاعات'),
    ]
    sender     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                    related_name='shares_sent')
    recipient  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                    related_name='shares_received')
    item_type  = models.CharField(max_length=8, choices=ITEM_TYPES)
    title      = models.CharField(max_length=240, blank=True, default='')
    payload    = models.JSONField(default=dict, blank=True)
    applied    = models.BooleanField(default=False,
                                      verbose_name='به گراف گیرنده اضافه شد')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'آیتم اشتراکی'
        verbose_name_plural = 'آیتم‌های اشتراکی'

    def __str__(self):
        return f'{self.sender} → {self.recipient}: {self.get_item_type_display()} {self.title}'


class ShareLink(models.Model):
    """Revocable, time-limited public link for a deliberately small person card."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='share_links')
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='share_links')
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ─────────────────────────────────────────────────────────────────
# 9. AlertAction
# ─────────────────────────────────────────────────────────────────

class AlertAction(models.Model):
    ACTION_CHOICES = [
        ('completed', 'انجام دادم'),
        ('dismissed', 'رد کردم'),
    ]
    alert_id   = models.CharField(max_length=120, db_index=True)
    alert_type = models.CharField(max_length=50, blank=True)
    node       = models.ForeignKey(Node, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='alert_actions')
    title      = models.CharField(max_length=300, blank=True)
    action     = models.CharField(max_length=20, choices=ACTION_CHOICES, default='dismissed')
    outcome    = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    owner      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='alert_actions',
        verbose_name='صاحب'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'اقدام روی هشدار'
        verbose_name_plural = 'اقدامات روی هشدارها'

    def __str__(self):
        return f"{self.get_action_display()} — {self.title[:60]}"


# ─────────────────────────────────────────────────────────────────
# 10. JournalImage
# ─────────────────────────────────────────────────────────────────

class JournalImage(models.Model):
    owner       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='journal_images', null=True, blank=True,
    )
    entry       = models.ForeignKey(JournalEntry, on_delete=models.CASCADE,
                                     related_name='images', null=True, blank=True)
    image       = models.ImageField(upload_to='journal/', verbose_name='تصویر')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']
        verbose_name = 'یادداشت روزانه'
        verbose_name_plural = 'یادداشت‌های روزانه'

    def clean(self):
        if self.entry_id and self.owner_id and self.entry.owner_id != self.owner_id:
            raise ValidationError('Journal image and entry must belong to the same owner.')

    def save(self, *args, **kwargs):
        if self.entry_id and not self.owner_id:
            self.owner_id = self.entry.owner_id
        self.clean()
        return super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────
# 11. Notification  (V3 — اطلاعیه‌های سیستم)
# ─────────────────────────────────────────────────────────────────

class Notification(models.Model):
    TYPES = [
        ('system', 'سیستمی'),
        ('sync',   'همگام‌سازی'),
        ('trust',  'امتیاز اعتماد'),
        ('alert',  'هشدار'),
    ]
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                    related_name='notifications')
    notif_type = models.CharField(max_length=20, choices=TYPES, default='system')
    message    = models.TextField()
    is_read    = models.BooleanField(default=False)
    link       = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'is_read'], name='notif_user_read')]
        verbose_name = 'اطلاعیه'
        verbose_name_plural = 'اطلاعیه‌ها'

    def __str__(self):
        return f"{self.user} — {self.message[:60]}"


# ─────────────────────────────────────────────────────────────────
# 12. SyncNotification  (V3 — همگام‌سازی با حساب‌های عمومی)
# ─────────────────────────────────────────────────────────────────

class SyncNotification(models.Model):
    STATUS = [
        ('pending',  'در انتظار'),
        ('accepted', 'قبول شد'),
        ('ignored',  'نادیده'),
        ('flagged',  'اشتباه'),
    ]
    recipient     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                       related_name='sync_notifications')
    from_user     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                       related_name='sent_syncs', null=True, blank=True)
    node_username = models.CharField(max_length=100)
    event_type    = models.CharField(max_length=30, default='new_data')
    payload       = models.JSONField(default=dict)
    status        = models.CharField(max_length=10, choices=STATUS, default='pending')
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'همگام‌سازی'
        verbose_name_plural = 'همگام‌سازی‌ها'

    def __str__(self):
        return f"sync: {self.node_username} → {self.recipient}"


# ─────────────────────────────────────────────────────────────────
# GiftBox  (V13 — جعبه‌ی هدیه مکعبی با امتیاز اعتماد)
# ─────────────────────────────────────────────────────────────────

class GiftBox(models.Model):
    SHARE_TYPES = [
        ('node', '👤 راس'),
        ('edge', '🔗 یال'),
        ('data', '📊 دیتا'),
    ]
    REACTION_CHOICES = [
        ('true',   '✅ راسته'),
        ('false',  '❌ دروغه'),
        ('accept', '🤐 قبولم'),
        ('reject', '\U0001f6ab رد میکنم'),
    ]

    sender        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                       related_name='giftboxes_sent')
    recipient     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                       related_name='giftboxes_received')
    share_type    = models.CharField(max_length=8, choices=SHARE_TYPES, default='node')
    payload       = models.JSONField(default=dict, blank=True)
    cube_faces    = models.JSONField(default=list, blank=True,
                                      verbose_name='پیکربندی ۶ وجه مکعب')
    reactions     = models.JSONField(default=dict, blank=True)
    my_reaction   = models.CharField(max_length=8, choices=REACTION_CHOICES,
                                      null=True, blank=True,
                                      verbose_name='واکنش گیرنده')
    opened        = models.BooleanField(default=False)
    content_added = models.BooleanField(default=False, verbose_name='اضافه شده به گراف')
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'اشتراک‌گذاری'
        verbose_name_plural = 'اشتراک‌گذاری‌ها'

    def __str__(self):
        return f'{self.sender} -> {self.recipient}: {self.share_type}'

    def reactions_dict(self):
        base = {'true': 0, 'false': 0, 'accept': 0, 'reject': 0}
        if isinstance(self.reactions, dict):
            base.update(self.reactions)
        return base


class PushSubscription(models.Model):
    """یک دستگاه/مرورگر که برای اعلان‌های Web Push ثبت‌نام کرده."""
    owner      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='push_subscriptions')
    endpoint   = models.URLField(max_length=500, unique=True)
    p256dh     = models.CharField(max_length=200)
    auth       = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'push<{self.owner_id}> {self.endpoint[:40]}'

    def as_subscription_info(self):
        return {
            'endpoint': self.endpoint,
            'keys': {'p256dh': self.p256dh, 'auth': self.auth},
        }


# ─────────────────────────────────────────────────────────────────
# Signals
# ─────────────────────────────────────────────────────────────────

@receiver(pre_delete, sender=User)
def clear_username_locked_on_user_delete(sender, instance, **kwargs):
    """
    وقتی یه کاربر public حذف می‌شه، نودهایی که از اون import شدن
    رو unlock می‌کنیم تا گیر نمونن.
    """
    Node.objects.filter(imported_from=instance).update(
        username_locked=False, imported_from=None
    )
