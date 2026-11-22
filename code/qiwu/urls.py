from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from django.views.generic import TemplateView

from .accounts import views as accounts
from .accounts.forms import CampusLoginForm
from .market import views as market

urlpatterns = [
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    path("accounts/register/", accounts.register, name="register"),
    path("accounts/register/verify/", accounts.verify_registration, name="verify_registration"),
    path(
        "accounts/login/",
        LoginView.as_view(template_name="accounts/login.html", authentication_form=CampusLoginForm),
        name="login",
    ),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("me/", market.my_records, name="my_records"),
    path("me/products/<uuid:pk>/", market.my_product, name="my_product"),
    path("me/reservations/<uuid:pk>/", market.my_reservation, name="my_reservation"),
    path("moderation/reviews/", market.review_queue, name="review_queue"),
]
