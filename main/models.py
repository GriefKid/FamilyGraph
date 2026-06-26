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
# 3. Node
# ─────────────────────────────────────────────────────────────────

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

    def clean(self):
        if self.source == self.target:
            raise ValidationError("A node cannot have a relationship with itself.")

    def save(self, *args, **kwargs):
        # جلوگیری از self-loop حتی در create برنامه‌نویسی (clean فقط در form صدا زده میشه)
        if self.source_id == self.target_id:
            raise ValidationError("A node cannot have a relationship with itself.")
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


# ─────────────────────────────────────────────────────────────────
# 5. Event
# ─────────────────────────────────────────────────────────────────

class Event(models.Model):
    title        = models.CharField(max_length=200)
    date         = models.DateField()
    description  = models.TextField(blank=True)
    participants = models.ManyToManyField(Node, blank=True, related_name='events')
    owner        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='events',
        verbose_name='صاحب'
    )

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-date']


# ─────────────────────────────────────────────────────────────────
# 6. Information
# ─────────────────────────────────────────────────────────────────

class Information(models.Model):
    VISIBILITY_CHOICES = [
        ('public',  'Everyone can see'),
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

    def __str__(self):
        return f'Information #{self.id} - {self.node}'


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
    text            = models.TextField(verbose_name='متن')
    entry_date      = models.DateField(null=True, blank=True, verbose_name='تاریخ رویداد')
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
    entry       = models.ForeignKey(JournalEntry, on_delete=models.CASCADE,
                                     related_name='images', null=True, blank=True)
    image       = models.ImageField(upload_to='journal/', verbose_name='تصویر')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']
        verbose_name = 'یادداشت روزانه'
        verbose_name_plural = 'یادداشت‌های روزانه'


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
