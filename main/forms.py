from django import forms
from .models import Node, Information, Relationship, Event


class NodeForm(forms.ModelForm):

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

