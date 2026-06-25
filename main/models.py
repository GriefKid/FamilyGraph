from django.core.exceptions import ValidationError
from django.db import models


class Group(models.Model):
    """گروه‌بندی دستی نودها — هر نود می‌تونه توی چند گروه باشه."""
    name  = models.CharField(max_length=100, unique=True, verbose_name='نام گروه')
    color = models.CharField(max_length=20, blank=True, verbose_name='رنگ',
                             help_text='کد رنگ hex مثل #6366f1 (اختیاری)')

    class Meta:
        ordering = ['name']
        verbose_name = 'گروه'
        verbose_name_plural = 'گروه‌ها'

    def __str__(self):
        return self.name


class Node(models.Model):
    username   = models.CharField(max_length=100, unique=True)
    first_name = models.CharField(max_length=100, blank=True, verbose_name='نام')
    last_name  = models.CharField(max_length=100, blank=True, verbose_name='نام خانوادگی')
    nickname   = models.CharField(max_length=100, blank=True, verbose_name='لقب / اسم مستعار')
    picture    = models.ImageField(upload_to="media/", blank=True, null=True)
    name       = models.CharField(max_length=200, blank=True)   # legacy — kept for compat
    birth_day  = models.DateField(blank=True, null=True)
    career     = models.CharField(max_length=200, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    group      = models.CharField(max_length=100, blank=True, verbose_name='گروه (قدیمی)',
                                  help_text='legacy — از groups استفاده کن')
    groups     = models.ManyToManyField(Group, blank=True, related_name='nodes',
                                        verbose_name='گروه‌ها')

    def display_name(self):
        """Best human-readable name for this node."""
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

class Relationship(models.Model):
    STATUS_CHOICES = [
        ('active',    'فعال'),
        ('distant',   'دور شده'),
        ('inactive',  'غیرفعال'),
    ]

    rel      = models.CharField(max_length=100, blank=True, null=True)
    source   = models.ForeignKey(Node, related_name='as_source', on_delete=models.PROTECT)
    target   = models.ForeignKey(Node, related_name='as_target', on_delete=models.PROTECT)
    strength = models.IntegerField(default=3, choices=[(i, i) for i in range(1, 6)])  # 1-5
    status   = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    met_at   = models.DateField(blank=True, null=True, verbose_name='تاریخ آشنایی')

    def __str__(self):
        return self.rel or f"{self.source} → {self.target}"

    def clean(self):
        if self.source == self.target:
            raise ValidationError("A node cannot have a relationship with itself.")

    class Meta:
        unique_together = ('source', 'target', 'rel')

class Event(models.Model):
    title        = models.CharField(max_length=200)
    date         = models.DateField()
    description  = models.TextField(blank=True)
    participants = models.ManyToManyField(Node, blank=True, related_name='events')

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-date']


class Information(models.Model):

    VISIBILITY_CHOICES = [
        ('public', 'Everyone can see'),
        ('shared', 'Someone can see'),
        ('private', 'Nobody'),
    ]

    node = models.ForeignKey(
        Node,
        on_delete=models.PROTECT,
        related_name='informations'
    )

    visibility = models.CharField(
        max_length=10,
        choices=VISIBILITY_CHOICES,
        default='private'
    )

    data = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f'Information #{self.id} - {self.node}'


class AppSettings(models.Model):
    """Singleton: stores app-wide settings like which node is 'me'."""
    root_node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='as_root',
        verbose_name='نود اصلی (من)',
    )

    def __str__(self):
        return f"تنظیمات (root: {self.root_node})"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    class Meta:
        verbose_name = 'تنظیمات'
        verbose_name_plural = 'تنظیمات'


class JournalEntry(models.Model):
    """Raw diary text saved when user submits journal."""
    text            = models.TextField(verbose_name='متن')
    entry_date      = models.DateField(null=True, blank=True, verbose_name='تاریخ رویداد')
    tags            = models.JSONField(default=list, blank=True, verbose_name='تگ‌ها')
    mood            = models.CharField(max_length=100, blank=True, verbose_name='خلق‌وخو')
    ai_analyzed     = models.BooleanField(default=False, verbose_name='آنالیز AI')
    mentioned_nodes = models.ManyToManyField('Node', blank=True, related_name='journal_entries')
    created_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"یادداشت {self.created_at.date()}: {self.text[:50]}"

    class Meta:
        ordering = ['-created_at']


class AlertAction(models.Model):
    """ثبت اقدام کاربر روی یک هشدار (رد کردن یا انجام دادن با نتیجه)."""
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
    outcome    = models.TextField(blank=True)   # نتیجه‌ای که کاربر ثبت کرد
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'اقدام روی هشدار'
        verbose_name_plural = 'اقدامات روی هشدارها'

    def __str__(self):
        return f"{self.get_action_display()} — {self.title[:60]}"


class JournalImage(models.Model):
    """Image attached to a journal entry."""
    entry       = models.ForeignKey(JournalEntry, on_delete=models.CASCADE,
                                    related_name='images', null=True, blank=True)
    image       = models.ImageField(upload_to='journal/', verbose_name='تصویر')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']
        verbose_name = 'یادداشت روزانه'
        verbose_name_plural = 'یادداشت‌های روزانه'

