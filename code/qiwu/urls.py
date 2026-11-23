from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from .accounts import views as accounts
from .accounts.forms import CampusLoginForm
from .market import views as market

urlpatterns = [
    path("", market.home, name="home"),
    path("products/<uuid:pk>/", market.public_product, name="public_product"),
    path("product-images/<uuid:pk>/", market.product_image, name="product_image"),
    path("sell/new/", market.edit_product, name="new_product"),
    path("sell/<uuid:pk>/edit/", market.edit_product, name="edit_product"),
    path("moderation/reviews/<uuid:pk>/", market.moderate, name="moderate"),
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
