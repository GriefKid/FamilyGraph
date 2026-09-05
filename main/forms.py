from django import forms
import re
from .models import Node, NodeContactDetails, Information, Relationship, Event
from .text_utils import finglish_slug
from .uploads import UploadValidationError, normalize_image_upload
from .utils_jalali import jalali_input_value, parse_date_input


class JalaliDateField(forms.Field):
    """A Persian-first date field that stores a normal Python date."""
    default_error_messages = {'invalid': 'تاریخ معتبر نیست؛ مثلاً ۱۴۰۴/۰۱/۰۱ وارد کن.'}

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', forms.TextInput(attrs={
            'class': 'form-control', 'dir': 'ltr',
            'inputmode': 'numeric', 'placeholder': '۱۴۰۴/۰۱/۰۱',
            'autocomplete': 'off',
        }))
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            return parse_date_input(value)
        except (TypeError, ValueError, OverflowError):
            raise forms.ValidationError(self.error_messages['invalid'])

    def prepare_value(self, value):
        if value and not isinstance(value, str):
            return jalali_input_value(value)
        return value


class NodeForm(forms.ModelForm):
    birth_day = JalaliDateField(label='تاریخ تولد', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A username is an internal identifier, not onboarding work for users.
        self.fields['username'].required = False
        self.fields['username'].widget.attrs['placeholder'] = 'اختیاری؛ خودکار ساخته می‌شود'

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        if username:
            return username
        # username is declared before the name fields, so those values are
        # not in cleaned_data yet during field-level validation.
        def submitted(field_name):
            return (self.cleaned_data.get(field_name)
                    or self.data.get(field_name, '')).strip()

        seed = ' '.join(filter(None, [
            submitted('first_name'),
            submitted('last_name'),
            submitted('nickname'),
            submitted('name'),
        ]))
        return finglish_slug(seed) or 'person'

    def clean_picture(self):
        picture = self.cleaned_data.get('picture')
        if not picture:
            return picture
        try:
            return normalize_image_upload(
                picture, max_bytes=8 * 1024 * 1024, max_dimension=2400,
                label='تصویر شخص',
            )
        except UploadValidationError as exc:
            raise forms.ValidationError(str(exc), code='invalid_image_upload') from exc

    class Meta:
        model = Node
        fields = ['username', 'first_name', 'last_name', 'nickname',
                  'picture', 'birth_day', 'career', 'phone_number', 'group', 'name']
        widgets = {
            'username':    forms.TextInput(attrs={'class': 'form-control'}),
            'first_name':  forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':   forms.TextInput(attrs={'class': 'form-control'}),
            'nickname':    forms.TextInput(attrs={'class': 'form-control'}),
            'career':      forms.TextInput(attrs={'class': 'form-control'}),
            'phone_number':forms.TextInput(attrs={'class': 'form-control'}),
            'group':       forms.TextInput(attrs={'class': 'form-control',
                                                  'placeholder': 'مثال: خانواده، کار، دوستان، داستان'}),
            'name':        forms.TextInput(attrs={'class': 'form-control',
                                                  'placeholder': 'نام قدیمی (اختیاری)'}),
        }


class NodeContactDetailsForm(forms.ModelForm):
    class Meta:
        model = NodeContactDetails
        fields = ['email', 'alternate_phone', 'bank_name', 'card_number',
                  'account_number', 'iban', 'telegram_username', 'whatsapp_number',
                  'instagram_username', 'x_username', 'linkedin_url', 'address', 'notes']
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'name@example.com'}),
            'alternate_phone': forms.TelInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': '09...'}),
            'bank_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثلاً بانک ملت'}),
            'card_number': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'inputmode': 'numeric', 'placeholder': '۱۶ رقم شماره کارت'}),
            'account_number': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'inputmode': 'numeric'}),
            'iban': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'inputmode': 'numeric', 'placeholder': 'IR...'}),
            'telegram_username': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': '@username'}),
            'whatsapp_number': forms.TelInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': '98912...'}),
            'instagram_username': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'username'}),
            'x_username': forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'username'}),
            'linkedin_url': forms.URLInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'https://linkedin.com/in/...'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'مثلاً کارت به کارت فقط به همین حساب'}),
        }

    @staticmethod
    def _latin_digits(value):
        return str(value or '').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))

    def clean_card_number(self):
        value = ''.join(ch for ch in self._latin_digits(self.cleaned_data.get('card_number')) if ch.isdigit())
        if value and len(value) != 16:
            raise forms.ValidationError('شماره کارت باید ۱۶ رقم باشد.')
        return value

    def clean_iban(self):
        value = self._latin_digits(self.cleaned_data.get('iban')).replace(' ', '').upper()
        if value and value.isdigit():
            value = 'IR' + value
        if value and (not value.startswith('IR') or len(value) != 26 or not value[2:].isdigit()):
            raise forms.ValidationError('شماره شبا باید با IR و ۲۴ رقم بعد از آن وارد شود.')
        return value

    @staticmethod
    def _clean_username(value, label, pattern):
        value = str(value or '').strip().lstrip('@')
        if value and not re.fullmatch(pattern, value):
            raise forms.ValidationError(f'{label} معتبر نیست.')
        return value

    def clean_telegram_username(self):
        return self._clean_username(
            self.cleaned_data.get('telegram_username'), 'نام کاربری تلگرام', r'[A-Za-z0-9_]{3,64}',
        )

    def clean_instagram_username(self):
        return self._clean_username(
            self.cleaned_data.get('instagram_username'), 'نام کاربری اینستاگرام', r'[A-Za-z0-9._]{1,64}',
        )

    def clean_x_username(self):
        return self._clean_username(
            self.cleaned_data.get('x_username'), 'نام کاربری X', r'[A-Za-z0-9_]{1,64}',
        )

    def clean_whatsapp_number(self):
        value = str(self.cleaned_data.get('whatsapp_number') or '').strip()
        value = self._latin_digits(value).replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if value.startswith('+'):
            value = value[1:]
        if value.startswith('00'):
            value = value[2:]
        if value.startswith('0'):
            value = '98' + value[1:]
        if value and (not value.isdigit() or len(value) < 10 or len(value) > 15):
            raise forms.ValidationError('شماره واتساپ معتبر نیست؛ کد کشور را هم وارد کن.')
        return value

class RelationshipForm(forms.ModelForm):
    met_at = JalaliDateField(label='تاریخ آشنایی', required=False)

    class Meta:
        model = Relationship
        fields = ['source', 'target', 'rel', 'strength', 'status', 'met_at']

        widgets = {
            "source":   forms.Select(attrs={"class": "form-select"}),
            "target":   forms.Select(attrs={"class": "form-select"}),
            "rel":      forms.TextInput(attrs={"class": "form-control"}),
            "strength": forms.Select(attrs={"class": "form-select"}),
            "status":   forms.Select(attrs={"class": "form-select"}),
        }

class EventForm(forms.ModelForm):
    date = JalaliDateField(label='تاریخ', required=True)
    class Meta:
        model = Event
        # owner is set programmatically in the view — never expose it
        fields = ['title', 'date', 'event_time', 'description', 'participants']
        widgets = {
            'title':        forms.TextInput(attrs={'class': 'form-control'}),
            'event_time':   forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'description':  forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'participants': forms.SelectMultiple(attrs={'class': 'form-select'}),
        }


class InformationForm(forms.ModelForm):
    class Meta:
        model = Information
        fields = "__all__"
        widgets = {
            'node': forms.Select(attrs={'class': 'form-control'}),
            'visibility': forms.Select(attrs={'class': 'form-control'}),
            'data': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 5,
                'placeholder': '{"key": "value"} یا متن ساده'
            }),
        }

