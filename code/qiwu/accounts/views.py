from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .forms import CampusEmailForm, RegistrationForm
from .services import register_user, send_registration_code


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = CampusEmailForm(
        request.POST if request.method == "POST" else None,
        initial={"email": request.session.get("registration_email", "")},
    )
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        try:
            send_registration_code(email)
        except ValidationError as exc:
            form.add_error("email", exc)
        else:
            request.session["registration_email"] = email
            return redirect("verify_registration")
    return render(
        request,
        "accounts/register.html",
        {
            "form": form,
            "local_mailbox": settings.EMAIL_BACKEND
            == "django.core.mail.backends.filebased.EmailBackend",
        },
    )


@sensitive_post_parameters("password", "code")
@require_http_methods(["GET", "POST"])
def verify_registration(request):
    if request.user.is_authenticated:
        return redirect("home")
    email = request.session.get("registration_email")
    if not email:
        return redirect("register")
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            register_user(email=email, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            del request.session["registration_email"]
            messages.success(request, "注册成功，请使用校园邮箱和密码登录。")
            return redirect("login")
    return render(
        request,
        "accounts/verify.html",
        {
            "form": form,
            "email": email,
            "local_mailbox": settings.EMAIL_BACKEND
            == "django.core.mail.backends.filebased.EmailBackend",
        },
    )
