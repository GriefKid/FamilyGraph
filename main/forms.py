from django import forms
from .models import Node, Information, Relationship


class NodeForm(forms.ModelForm):

    class Meta:
        model = Node
        fields = "__all__"

class RelationshipForm(forms.ModelForm):

    class Meta:
        model = Relationship
        fields = "__all__"

        widgets = {
            "father": forms.Select(attrs={"class": "form-select"}),
            "child": forms.Select(attrs={"class": "form-select"}),
            "rel": forms.TextInput(attrs={"class": "form-control"}),
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

