from django import forms
from .models import UserProfile


class LoginForm(forms.Form):
    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your username',
            'autocomplete': 'username',
        })
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your password',
            'autocomplete': 'current-password',
        })
    )


class UserProfileForm(forms.ModelForm):
    signature_data = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = UserProfile
        fields = ['signature']
        widgets = {
            'signature': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*',
            })
        }
