from django import forms
from .models import Node, Information, Relationship, Event


class NodeForm(forms.ModelForm):

    class Meta:
        model = Node
        fields = ['username', 'first_name', 'last_name', 'nickname',
                  'picture', 'birth_day', 'career', 'phone_number', 'name']
        widgets = {
            'username':    forms.TextInput(attrs={'class': 'form-control'}),
            'first_name':  forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':   forms.TextInput(attrs={'class': 'form-control'}),
            'nickname':    forms.TextInput(attrs={'class': 'form-control'}),
            'birth_day':   forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'career':      forms.TextInput(attrs={'class': 'form-control'}),
            'phone_number':forms.TextInput(attrs={'class': 'form-control'}),
            'name':        forms.TextInput(attrs={'class': 'form-control',
                                                  'placeholder': 'نام قدیمی (اختیاری)'}),
        }

class RelationshipForm(forms.ModelForm):

    class Meta:
        model = Relationship
        fields = "__all__"

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
        fields = '__all__'
        widgets = {
            'title':        forms.TextInput(attrs={'class': 'form-control'}),
            'date':         forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
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

