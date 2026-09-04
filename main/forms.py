from django import forms
from django.utils.text import slugify
from .models import Node, Information, Relationship, Event
from .uploads import UploadValidationError, normalize_image_upload


class NodeForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A username is an internal identifier, not onboarding work for users.
        self.fields['username'].required = False
        self.fields['username'].widget.attrs['placeholder'] = 'اختیاری؛ خودکار ساخته می‌شود'

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        if username:
            return username
        seed = ' '.join(filter(None, [
            self.cleaned_data.get('first_name', ''),
            self.cleaned_data.get('last_name', ''),
            self.cleaned_data.get('nickname', ''),
            self.cleaned_data.get('name', ''),
        ]))
        return slugify(seed, allow_unicode=True)[:92] or 'person'

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
            'birth_day':   forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'career':      forms.TextInput(attrs={'class': 'form-control'}),
            'phone_number':forms.TextInput(attrs={'class': 'form-control'}),
            'group':       forms.TextInput(attrs={'class': 'form-control',
                                                  'placeholder': 'مثال: خانواده، کار، دوستان، داستان'}),
            'name':        forms.TextInput(attrs={'class': 'form-control',
                                                  'placeholder': 'نام قدیمی (اختیاری)'}),
        }

class RelationshipForm(forms.ModelForm):

    class Meta:
        model = Relationship
        fields = ['source', 'target', 'rel', 'strength', 'status', 'met_at']

        widgets = {
            "source":   forms.Select(attrs={"class": "form-select"}),
            "target":   forms.Select(attrs={"class": "form-select"}),
            "rel":      forms.TextInput(attrs={"class": "form-control"}),
            "strength": forms.Select(attrs={"class": "form-select"}),
            "status":   forms.Select(attrs={"class": "form-select"}),
            "met_at":   forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }

class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        # owner is set programmatically in the view — never expose it
        fields = ['title', 'date', 'event_time', 'description', 'participants']
        widgets = {
            'title':        forms.TextInput(attrs={'class': 'form-control'}),
            'date':         forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
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

