from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password

from .validators import CAMPUS_EMAIL_HINT, campus_email


class CampusEmailForm(forms.Form):
    email = forms.EmailField(
        label="南京大学邮箱",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "输入完整校园邮箱"}),
        help_text=CAMPUS_EMAIL_HINT,
    )

    def clean_email(self):
        return campus_email(self.cleaned_data["email"])


class RegistrationForm(forms.Form):
    code = forms.RegexField(
        label="邮箱验证码",
        regex=r"^[0-9]{6}$",
        max_length=6,
        error_messages={"invalid": "请输入邮件中的 6 位数字验证码。"},
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
    )
    display_name = forms.CharField(
        label="昵称", max_length=20, help_text="其他同学将看到这个名字。"
    )
    password = forms.CharField(
        label="设置密码",
        strip=False,
        max_length=128,
        validators=[validate_password],
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="至少 8 位，同时包含字母和数字。",
    )


class CampusLoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="南京大学邮箱",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}),
    )
    password = forms.CharField(
        label="密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    error_messages = {
        "invalid_login": "邮箱或密码不正确，或账号已停用。",
        "inactive": "账号已停用。",
    }

    def clean_username(self):
        return campus_email(self.cleaned_data["username"])
