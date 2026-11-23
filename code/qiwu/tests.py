from django.test import TestCase
from django.urls import reverse


class HomePageTests(TestCase):
    def test_public_home_displays_the_marketplace(self):
        response = self.client.get(reverse("home"))

        self.assertTemplateUsed(response, "home.html")
        self.assertContains(response, "栖物")
        self.assertContains(response, "商品大厅")
